"""Conservative host-volume budgets for image work; no VM or disk mutations."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

GIB = 1024 ** 3
HOST_RESERVE = 4 * GIB
STATE_BUDGET = 256 * 1024 ** 2
IMAGE_BUDGET = 15 * GIB
VM_BUDGET = 35 * GIB
STOCK_GUEST_RESERVE = GIB


def stock_guest_requirement(bundle_bytes, unpacked_bytes, raw_bytes):
    """Budget both input copies and simultaneous raw/compressed output in VM."""
    for value in (bundle_bytes, unpacked_bytes, raw_bytes):
        if type(value) is not int or value <= 0:
            raise ValueError('Stock guest byte sizes must be positive integers')
    # A compressed output can approach the raw input's size. Retain a full raw
    # equivalent plus 1 GiB for compression framing, metadata and VM services.
    return bundle_bytes + unpacked_bytes + 2 * raw_bytes + STOCK_GUEST_RESERVE


def check_stock_guest_space(free_bytes, bundle_bytes, unpacked_bytes, raw_bytes):
    if type(free_bytes) is not int or free_bytes < 0:
        raise ValueError('Guest free space must be a nonnegative integer')
    needed = stock_guest_requirement(bundle_bytes, unpacked_bytes, raw_bytes)
    if free_bytes < needed:
        raise RuntimeError(f'Stock build needs {needed / GIB:.2f} GiB free inside the Linux VM; '
                           f'only {free_bytes / GIB:.2f} GiB available. Preserve or export '
                           'retained image jobs before freeing their generated guest files.')
    return {'required_bytes': needed, 'free_bytes': free_bytes}


def existing_parent(path):
    """Resolve paths without silently accepting an unavailable external disk."""
    path = Path(path).expanduser().absolute()
    for item in (path, *path.parents):
        if item.is_symlink() and not item.exists():
            raise RuntimeError('Unavailable storage (dangling symlink): ' + str(item))
        if item.exists():
            return item.resolve()
    raise RuntimeError('No existing storage parent for ' + str(path))


def check_budgets(requests):
    """Sum reservations on the same filesystem instead of checking each alone."""
    grouped = {}
    for path, amount, purpose in requests:
        resolved = existing_parent(path)
        device = resolved.stat().st_dev
        group = grouped.setdefault(device, {'path': resolved, 'needed': 0, 'purposes': []})
        group['needed'] += amount
        group['purposes'].append(purpose)
    report = []
    for group in grouped.values():
        free = shutil.disk_usage(group['path']).free
        purpose = ', '.join(group['purposes'])
        if free < group['needed']:
            raise RuntimeError(f"Storage for {purpose} needs {group['needed'] / GIB:.2f} GiB free "
                               f"on {group['path']}; only {free / GIB:.2f} GiB available")
        report.append({'path': str(group['path']), 'needed_bytes': group['needed'],
                       'free_bytes': free, 'purposes': group['purposes']})
    return report


def preflight(root, storage, operation='build', export_bytes=None, input_bytes=0):
    """Check internal host reserve, build state, output, and backing-disk volume.

    Status/log/wait commands should not call this: low storage must not prevent
    observing a running job. Export can provide its measured transfer size.
    """
    if operation not in {'build', 'prepare', 'export'}:
        raise ValueError('Unknown resource operation: ' + operation)
    root, storage = Path(root), Path(storage)
    if not storage.is_dir():
        raise RuntimeError('Image storage is unavailable: ' + str(storage))
    host = Path('/private/var') if sys.platform == 'darwin' else Path.home()
    requests = [(host, HOST_RESERVE, 'internal host reserve'),
                (root / 'build', STATE_BUDGET, 'Lima/project metadata')]
    output_budget = IMAGE_BUDGET
    if operation == 'export' and export_bytes is not None:
        if not isinstance(export_bytes, int) or export_bytes < 0:
            raise ValueError('Export size must be a nonnegative integer')
        output_budget = 2 * export_bytes + GIB
    if not isinstance(input_bytes, int) or input_bytes < 0:
        raise ValueError('Input size must be a nonnegative integer')
    # Retained per-job bundles are additional to download caches and exports.
    requests.append((storage, output_budget + input_bytes, 'image inputs/export'))
    if operation == 'build':
        disk = root / 'build/lima/tyrell-build/disk'
        backing = existing_parent(disk)
        if (disk.parent / 'lima.yaml').exists() and not disk.exists():
            raise RuntimeError('Existing builder disk is unavailable: ' + str(disk))
        requests.append((backing.parent if backing.is_file() else backing,
                         VM_BUDGET + input_bytes, 'VM disk growth'))
    return check_budgets(requests)


def host_preflight(root):
    """Small boot/metadata reserve also applies to package-only builder starts."""
    root = Path(root)
    host = Path('/private/var') if sys.platform == 'darwin' else Path.home()
    disk = root / 'build/lima/tyrell-build/disk'
    existing_parent(disk)  # Detect a missing external backing disk before Lima.
    if (disk.parent / 'lima.yaml').exists() and not disk.exists():
        raise RuntimeError('Existing builder disk is unavailable: ' + str(disk))
    return check_budgets([(host, HOST_RESERVE, 'internal host reserve'),
                          (root / 'build', STATE_BUDGET, 'Lima/project metadata')])


def memory_mib(value):
    """Accept explicit Lima-sized units, bounded to avoid accidental overcommit."""
    match = re.fullmatch(r'([1-9][0-9]*)(MiB|GiB)', value or '')
    if not match:
        raise RuntimeError('TYRELL_BUILDER_MEMORY must use MiB or GiB, for example 4GiB')
    amount = int(match[1]) * (1024 if match[2] == 'GiB' else 1)
    if not 2048 <= amount <= 32768 or amount % 256:
        raise RuntimeError('Builder memory must be 2–32 GiB in increments of 256 MiB')
    return amount


def check_memory_capacity(amount_mib):
    """Leave 2 GiB of physical RAM for the host; this is not a free-RAM claim."""
    if sys.platform == 'darwin':
        total = int(subprocess.check_output(['sysctl', '-n', 'hw.memsize'], text=True).strip())
    else:
        total = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    if amount_mib * 1024 ** 2 + 2 * GIB > total:
        raise RuntimeError('Requested builder RAM leaves less than 2 GiB physical RAM for the host')
