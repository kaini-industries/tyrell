"""Immutable byte identities, frozen export linkage and safe archive regressions."""
import copy
import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("release_candidate", Path(__file__).resolve().parents[1] / "candidate.py")
candidate = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(candidate)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tar_bytes(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name); member.size = len(data); member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def deb_bytes(package="tyrell-fixture", version="1.0-1", architecture="arm64"):
    control = f"Package: {package}\nVersion: {version}\nArchitecture: {architecture}\nDescription: fixture\n".encode()
    members = {"debian-binary": b"2.0\n", "control.tar":tar_bytes({"./control":control}), "data.tar":tar_bytes({"usr/bin/app":b"fixture"})}
    output = b"!<arch>\n"
    for name, data in members.items():
        header = f"{name+'/':<16}{0:<12}{0:<6}{0:<6}{'100644':<8}{len(data):<10}`\n".encode()
        assert len(header) == 60
        output += header + data + (b"\n" if len(data)%2 else b"")
    return output


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(); self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve(); self.source = self.root/"source"; self.source.mkdir()
        for name in ("image/profiles/tyrell-zero.json", "locks/tyrell-zero-apt.json"):
            path=self.source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b"{}\n")
        for args in (("init","-q"),("add","."),("-c","user.name=Fixture","-c","user.email=fixture@example.invalid","commit","-qm","fixture")):
            subprocess.run(["git","-C",str(self.source),*args],check=True,capture_output=True)
        self.revision=subprocess.check_output(["git","-C",str(self.source),"rev-parse","HEAD"],text=True).strip()
        self.inputs=self.root/"inputs";self.inputs.mkdir();self.output=self.root/"output"
        package="checks/evidence/package.deb"; self.write(package,deb_bytes());package_sha=candidate.digest(self.inputs/package)
        report={"schema":1,"suite":"chess-native","status":"passed","scope":"source-checks-only",
                "source":{"commit":self.revision,"dirty":False},
                "results":[{"kind":"package-build","status":"passed","path":"build/ci/packages/package.deb","sha256":package_sha}],
                "exported_evidence":[{"original_path":"build/ci/packages/package.deb","artifact_path":"evidence/package.deb","sha256":package_sha}]}
        self.write("checks/chess-native.json",candidate.canonical(report))
        row={"artifact_sha256":package_sha,"target":"original-m5","gate":"app-acceptance","result":"not-tested","evidence_scope":"not-tested","evidence":[],"limitations":["No physical device used"]}
        self.write("metadata/qualification.json",candidate.canonical({"schema":1,"records":[row]}))
        self.documents=[]
        for role,name,body in [("qualification","metadata/qualification.json",None),("known-issues","metadata/known-issues.md",b"Device acceptance remains pending.\n"),
                               ("recovery","metadata/recovery.md",b"Retain original media; manually select reviewed image.\n"),("license","metadata/LICENSE.txt",b"Fixture license notices\n"),
                               ("inventory","metadata/inventory.tsv",b"package\t1.0-1\tarm64\n"),("input-manifest","metadata/inputs.json",b"{\"fixture\":true}\n")]:
            if body is not None:self.write(name,body)
            self.documents.append(dict(self.desc(name),role=role))
        self.selection={"schema":1,"release_version":"fixture-1.0-dev.1","source_commit":self.revision,"target":"original-m5","optimized_power":False,
                        "packages":[dict(self.desc(package),package="tyrell-fixture",version="1.0-1",architecture="arm64",build_report="checks/chess-native.json")],
                        "checks":[self.desc("checks/chess-native.json")],"documents":self.documents,"image":None}

    def write(self,name,data):
        path=self.inputs/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return path

    def desc(self,name):
        return {"path":name,"sha256":candidate.digest(self.inputs/name)}

    def assemble(self,selection=None,output=None):
        return candidate.assemble(selection or self.selection,self.inputs,output or self.output,repository_root=self.source)

    def change_document(self,role,body):
        item=next(d for d in self.documents if d['role']==role)
        self.write(item['path'],body);item['sha256']=candidate.digest(self.inputs/item['path'])

    def add_image(self, preset='baseline'):
        self.selection['target']='tyrell-zero'
        directory=self.inputs/"image-export";directory.mkdir()
        files={name:(self.source/name).read_bytes() for name in ("image/profiles/tyrell-zero.json","locks/tyrell-zero-apt.json")}
        files["apt-repository/apt-lock.json"]=files["locks/tyrell-zero-apt.json"]
        identities={name:{"sha256":sha(body),"bytes":len(body),"mode":0o644} for name,body in sorted(files.items())}
        metadata={"schema":1,"profile_id":"tyrell-zero","build_mode":"zero-native","source_commit":self.revision,"source_dirty":False,"selected_power_preset":preset,
                  "job_id":"20261124T123456000000Z-12345678","files":identities,"capture_sha256":sha(json.dumps(identities,sort_keys=True).encode()),
                  "profile_sha256":sha(files["image/profiles/tyrell-zero.json"]),"locks_sha256":{"tyrell-zero-apt.json":sha(files["locks/tyrell-zero-apt.json"])}}
        archive=tar_bytes({**files,"image-metadata.json":candidate.canonical(metadata)})
        self.write("image-export/image-input.tar",archive)
        raw=b"bounded synthetic image payload\n"*50; compressed=lzma.compress(raw)
        self.write("image-export/candidate.img",raw);self.write("image-export/candidate.img.xz",compressed)
        self.write("image-export/build-inputs.json",candidate.canonical(metadata))
        identity={"job_id":metadata["job_id"],"attempt_id":1,"bundle_sha256":sha(archive)}
        self.write("image-export/attempt.json",candidate.canonical(identity))
        verification={"status":"passed","image":{"name":"candidate.img","bytes":len(raw),"sha256_before":sha(raw),"sha256_after":sha(raw)}}
        self.write("image-export/image-verification.json",candidate.canonical(verification))
        guest={"kind":"Tyrell Debian image","inputs":metadata,"verification":verification,"compressed_payload_sha256":sha(raw),
               "sha256":{p.relative_to(directory).as_posix():candidate.digest(p) for p in directory.iterdir() if p.name!='image-input.tar'}}
        self.write("image-export/guest-image-manifest.json",candidate.canonical(guest));guest_sha=candidate.digest(directory/"guest-image-manifest.json")
        host=copy.deepcopy(guest);host["sha256"]["image-input.tar"]=sha(archive)
        host["provenance"]={**identity,"input_archive":"image-input.tar","guest_manifest_sha256":guest_sha}
        self.write("image-export/image-manifest.json",candidate.canonical(host))
        self.rehash_image()
        self.selection['image']={"directory":"image-export","manifest_sha256":candidate.digest(directory/'image-manifest.json'),"input_archive_sha256":sha(archive),"guest_manifest_sha256":guest_sha}
        row={"artifact_sha256":sha(compressed),"target":"tyrell-zero","gate":"z4","result":"not-tested","evidence_scope":"not-tested","evidence":[],"limitations":["No physical acceptance"]}
        qual=json.loads((self.inputs/'metadata/qualification.json').read_text());qual['records'].append(row)
        qual['records'][0]['target']='tyrell-zero'
        self.change_document('qualification',candidate.canonical(qual))
        return directory

    def rehash_image(self):
        directory=self.inputs/'image-export'
        sums={p.name:candidate.digest(p) for p in directory.iterdir() if p.name!='SHA256SUMS'}
        self.write('image-export/SHA256SUMS',''.join(f'{v}  {n}\n' for n,v in sorted(sums.items())).encode())

    def test_deterministic_candidate_and_safe_roundtrip(self):
        result=self.assemble(); second=self.assemble(output=self.root/'other')
        self.assertEqual(result['candidate_id'],second['candidate_id']);self.assertEqual(result['archive_sha256'],second['archive_sha256'])
        self.assertFalse(result['hardware_qualified']);self.assertFalse(result['promotable'])
        manifest=candidate.verify_directory(result['directory'],result['candidate_id'])
        self.assertEqual(manifest['assets'][0]['package'],'tyrell-fixture')
        unpack=candidate.verify_archive(result['archive'],result['archive_sha256'],result['candidate_id'],self.root/'unpack')
        self.assertEqual(candidate.verify_directory(unpack['directory']),manifest)

    def test_preserve_existing_outputs(self):
        result=self.assemble(); original=Path(result['archive']).read_bytes()
        with self.assertRaisesRegex(ValueError,'Preserving existing'):self.assemble()
        self.assertEqual(Path(result['archive']).read_bytes(),original)
        with self.assertRaisesRegex(ValueError,'Preserving existing'):candidate.verify_archive(result['archive'],result['archive_sha256'],result['candidate_id'],self.output)

    def test_preserve_an_earlier_partial_archive(self):
        result=self.assemble();other=self.root/'other';other.mkdir()
        partial=other/('.candidate-'+result['candidate_id']+'.partial');partial.write_bytes(b'earlier retained work')
        with self.assertRaises(FileExistsError):self.assemble(output=other)
        self.assertEqual(partial.read_bytes(),b'earlier retained work')

    def test_exact_source_commit_and_clean_checkout(self):
        self.selection['source_commit']='0'*40
        with self.assertRaisesRegex(ValueError,'clean checkout'):self.assemble()
        self.selection['source_commit']=self.revision;(self.source/'changed').write_text('dirty')
        with self.assertRaisesRegex(ValueError,'clean checkout'):self.assemble()

    def test_package_bytes_control_and_build_report_are_all_bound(self):
        self.selection['packages'][0]['version']='2-1'
        with self.assertRaisesRegex(ValueError,'Debian identity'):self.assemble()
        self.selection['packages'][0]['version']='1.0-1'
        report=json.loads((self.inputs/'checks/chess-native.json').read_text());report['results'][0]['sha256']='0'*64
        self.write('checks/chess-native.json',candidate.canonical(report));self.selection['checks']=[self.desc('checks/chess-native.json')]
        with self.assertRaisesRegex(ValueError,'passing build result'):self.assemble()

    def test_old_source_dirty_failed_or_duplicate_checks_rejected(self):
        for mutation in ({'status':'failed'},{'source':{'commit':'1'*40,'dirty':False}},{'source':{'commit':self.revision,'dirty':True}}):
            report=json.loads((self.inputs/'checks/chess-native.json').read_text());old=copy.deepcopy(report);report.update(mutation)
            self.write('checks/chess-native.json',candidate.canonical(report));self.selection['checks']=[self.desc('checks/chess-native.json')]
            with self.assertRaisesRegex(ValueError,'exact clean'):self.assemble()
            self.write('checks/chess-native.json',candidate.canonical(old));self.selection['checks']=[self.desc('checks/chess-native.json')]
        self.selection['checks']*=2
        with self.assertRaisesRegex(ValueError,'Duplicate selected check'):self.assemble()

    def test_changed_exported_evidence_fails(self):
        self.write('checks/evidence/package.deb',b'changed package')
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):self.assemble()

    def test_missing_qualification_gates_stay_visible(self):
        self.change_document('qualification',candidate.canonical({'schema':1,'records':[]}))
        with self.assertRaisesRegex(ValueError,'Missing qualification gates'):self.assemble()

    def test_document_roles_and_evidence_paths(self):
        self.selection['documents']=[d for d in self.documents if d['role']!='license']
        with self.assertRaisesRegex(ValueError,'documentation missing'):self.assemble()
        self.selection['documents']=self.documents
        qual=json.loads((self.inputs/'metadata/qualification.json').read_text());qual['records'][0].update(result='passed',evidence_scope='device-tested',evidence=[{'path':'missing.md','sha256':'0'*64}])
        self.change_document('qualification',candidate.canonical(qual))
        with self.assertRaisesRegex(ValueError,'Missing candidate input'):self.assemble()

    def test_traversal_symlink_duplicate_and_reserved_paths_rejected(self):
        for name in ('../outside','/tmp/file','a//b','a/./b','a\\b','line\nbreak'):
            with self.assertRaises(ValueError):candidate.relative(name)
        link=self.inputs/'linked';link.symlink_to(self.inputs/'checks',target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'Symlinked'):candidate.safe_path(self.inputs,'linked/chess-native.json')
        self.write('release-manifest.json',b'reserved')
        self.selection['documents'].append(dict(self.desc('release-manifest.json'),role='evidence'))
        with self.assertRaisesRegex(ValueError,'Reserved'):self.assemble()

    def test_manifest_tamper_or_extra_file_fails(self):
        result=self.assemble();directory=Path(result['directory'])
        (directory/'extra').write_text('not in inventory')
        with self.assertRaisesRegex(ValueError,'exact directory'):candidate.verify_directory(directory,result['candidate_id'])
        (directory/'extra').unlink();path=directory/'metadata/known-issues.md';path.write_text('tampered')
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):candidate.verify_directory(directory,result['candidate_id'])

    def test_archive_rejects_hash_mismatch_links_duplicates_and_traversal(self):
        result=self.assemble()
        with self.assertRaisesRegex(ValueError,'archive checksum'):candidate.verify_archive(result['archive'],'0'*64,result['candidate_id'],self.root/'bad')
        for kind in ('traversal','symlink','duplicate'):
            archive=self.root/(kind+'.tar')
            with tarfile.open(archive,'w') as out:
                member=tarfile.TarInfo('../escape' if kind=='traversal' else 'entry')
                if kind=='symlink':member.type=tarfile.SYMTYPE;member.linkname='/tmp/escape'
                else:member.size=1
                out.addfile(member,None if kind=='symlink' else io.BytesIO(b'x'))
                if kind=='duplicate':out.addfile(member,io.BytesIO(b'x'))
            with self.assertRaises(ValueError):candidate.verify_archive(archive,candidate.digest(archive),result['candidate_id'],self.root/('bad-'+kind))

    def test_frozen_zero_export_reuses_contract_and_payload_binding(self):
        self.add_image();result=self.assemble();manifest=candidate.verify_directory(result['directory'],result['candidate_id'])
        image=next(a for a in manifest['assets'] if a['kind']=='image')
        self.assertEqual(image['raw_sha256'],image['compressed_payload_sha256'])
        self.assertEqual(image['input_archive_sha256'],self.selection['image']['input_archive_sha256'])

    def test_self_rehashed_compressed_payload_cannot_change_verified_raw(self):
        directory=self.add_image();self.write('image-export/candidate.img.xz',lzma.compress(b'changed'))
        # Rehash every exported wrapper as an adversarial substitute would.
        guest=json.loads((directory/'guest-image-manifest.json').read_text());guest['sha256']['candidate.img.xz']=candidate.digest(directory/'candidate.img.xz')
        self.write('image-export/guest-image-manifest.json',candidate.canonical(guest));guest_sha=candidate.digest(directory/'guest-image-manifest.json')
        host=json.loads((directory/'image-manifest.json').read_text());host['sha256']['candidate.img.xz']=candidate.digest(directory/'candidate.img.xz');host['provenance']['guest_manifest_sha256']=guest_sha
        self.write('image-export/image-manifest.json',candidate.canonical(host));self.rehash_image()
        self.selection['image'].update(manifest_sha256=candidate.digest(directory/'image-manifest.json'),guest_manifest_sha256=guest_sha)
        with self.assertRaisesRegex(ValueError,'Compressed image payload'):self.assemble()

    def test_selected_image_manifest_cannot_be_silently_rebound(self):
        directory=self.add_image();path=directory/'image-manifest.json';path.write_bytes(path.read_bytes()+b' ');self.rehash_image()
        with self.assertRaisesRegex(ValueError,'selected manifest'):self.assemble()

    def test_optimized_image_cannot_understate_captured_power_or_hide_z6(self):
        self.add_image(preset='ethernet')
        with self.assertRaisesRegex(ValueError,'captured image power preset'):self.assemble()
        self.selection['optimized_power']=True
        with self.assertRaisesRegex(ValueError,'Missing qualification gates'):self.assemble()
        qual=json.loads((self.inputs/'metadata/qualification.json').read_text())
        row=copy.deepcopy(qual['records'][-1]);row['gate']='z6';qual['records'].append(row)
        self.change_document('qualification',candidate.canonical(qual))
        result=self.assemble();manifest=candidate.verify_directory(result['directory'],result['candidate_id'])
        self.assertTrue(manifest['optimized_power'])
        self.assertFalse(manifest['hardware_qualified'])

    def test_package_build_export_binding_is_rechecked_on_verification(self):
        result=self.assemble();directory=Path(result['directory'])
        path=directory/'release-manifest.json';manifest=candidate.read_json(path)
        manifest['assets'][0]['build_report']='different/report.json';path.write_bytes(candidate.canonical(manifest))
        sums={name:item['sha256'] for name,item in manifest['files'].items()};sums['release-manifest.json']=candidate.digest(path)
        (directory/'SHA256SUMS').write_text(''.join(f'{v}  {n}\n' for n,v in sorted(sums.items())))
        with self.assertRaisesRegex(ValueError,'exact passing build export'):candidate.verify_directory(directory)

    def test_inventory_entry_cannot_redirect_hash_verification(self):
        result=self.assemble();directory=Path(result['directory']);path=directory/'release-manifest.json';manifest=candidate.read_json(path)
        name='metadata/known-issues.md';other='metadata/recovery.md'
        (directory/name).write_bytes(b'unverified changed bytes')
        manifest['files'][name]={**manifest['files'][other],'role':'known-issues','path':other}
        path.write_bytes(candidate.canonical(manifest))
        sums={n:item['sha256'] for n,item in manifest['files'].items()};sums['release-manifest.json']=candidate.digest(path)
        (directory/'SHA256SUMS').write_text(''.join(f'{v}  {n}\n' for n,v in sorted(sums.items())))
        with self.assertRaisesRegex(ValueError,'Invalid candidate inventory'):candidate.verify_directory(directory)

    def test_cli_report_cannot_mutate_an_immutable_candidate(self):
        result=self.assemble();directory=Path(result['directory']);before=(directory/'release-manifest.json').read_bytes()
        with self.assertRaises(SystemExit):candidate.main(['verify','--directory',str(directory),'--candidate-id',result['candidate_id'],'--report',str(directory/'release-manifest.json')])
        self.assertEqual((directory/'release-manifest.json').read_bytes(),before)

    def test_debian_identity_parser_rejects_malformed_architecture_and_truncation(self):
        for data in (b'not-deb',deb_bytes(architecture='amd64'),deb_bytes()[:-512]):
            path=self.write('malformed.deb',data)
            with self.assertRaises(ValueError):candidate.package_identity(path)


if __name__=='__main__':unittest.main()
