"""Adversarial protocol/lifecycle tests with real DEB bytes and isolated roots.

Only package execution is substituted. Archive inspection/version comparisons use
the host's real dpkg tools; production has no executor/root override interface.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import guard as g


def host_run(args, timeout=600):
    executable = shutil.which(Path(args[0]).name)
    result = subprocess.run([executable, *args[1:]], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return result.returncode, result.stdout


def create_deb(directory, version, *, name="tyrell-video", path="usr/bin/tyrell-video", script=True, depends="libc6 (>= 2.38)"):
    tree = directory / (name + "-" + version)
    (tree / "DEBIAN").mkdir(parents=True)
    (tree / "DEBIAN/control").write_text("Package: " + name + "\nVersion: " + version + "\nArchitecture: arm64\nMaintainer: Test <test@localhost>\nDescription: isolated protocol fixture\nDepends: " + depends + "\n")
    if script:
        (tree / "DEBIAN/postinst").write_text("#!/bin/sh\n# reviewed fixture " + version + "\nexit 0\n")
        (tree / "DEBIAN/postinst").chmod(0o755)
    payload = tree / path
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_text(version + "\n");payload.chmod(0o755)
    output = directory / (name + "_" + version + ".deb")
    rc, text = host_run(["/usr/bin/dpkg-deb", "--root-owner-group", "--build", str(tree), str(output)])
    if rc:raise RuntimeError(text.decode())
    return output


class FakePackages:
    def __init__(self, root, records, archives):
        self.root = root;self.records = records;self.archives = archives;self.calls = []
        self.states = {name:{"state":"ii ", "version":"1", "arch":"arm64", "provides":"", "multi":""}
                       for name in ["applaunch", "tyrell-zero-session", "tyrell-zero-store"]}
        self.states["libc6"] = {"state":"ii ", "version":"2.41-12", "arch":"arm64", "provides":"", "multi":"same"}
        self.fail_install = set();self.fail_configure = False;self.apt = b"";self.audit_override = None

    def present(self, record, state="ii "):
        self.states[record["package"]] = {"state":state,"version":record["version"],"arch":"arm64","provides":"","multi":""}
        folder = self.root / "var/lib/dpkg/info";folder.mkdir(parents=True, exist_ok=True)
        rc, blob = host_run(["/usr/bin/dpkg-deb", "--ctrl-tarfile", str(self.archives[record["version"]])])
        import io,tarfile
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:*") as tar:
            for item in tar:
                name = g.archive_name(item.name)
                if name in g.SCRIPTS:(folder / (record["package"] + "." + name)).write_bytes(tar.extractfile(item).read())

    def __call__(self, args, timeout=600):
        self.calls.append(args)
        tool = Path(args[0]).name
        if tool == "dpkg-deb":return host_run(args, timeout)
        if tool == "apt-get":return 0, self.apt
        if tool == "dpkg-query":
            fmt = next(a for a in args if a.startswith("-f="))
            if "${Provides}" in fmt:
                return 0, "".join("\t".join([name,v["state"],v["version"],v["arch"],v["multi"],v["provides"]])+"\n" for name,v in self.states.items()).encode()
            if "${Package}" in fmt:
                return 0, "".join("\t".join([name,v["state"],v["version"]])+"\n" for name,v in self.states.items()).encode()
            name = args[-1];state = self.states.get(name)
            if not state:return 1,b"unknown"
            return 0,"\t".join([state["state"],state["version"],state["arch"]]).encode()
        if tool != "dpkg":raise AssertionError(args)
        args = [a for a in args[1:] if not a.startswith(("--root=", "--log="))]
        if "--compare-versions" in args:return host_run(["/usr/bin/dpkg",*args])
        if "--audit" in args:
            if self.audit_override is not None:return 0,self.audit_override
            broken = [n for n,s in self.states.items() if s["state"][1] not in "inc"]
            return 0,("Pending configuration:\n"+"".join(" " + n + " pending\n" for n in broken)).encode() if broken else b""
        if "--install" in args:
            for filename in args[args.index("--install")+1:]:
                sha = g.digest(Path(filename).read_bytes())
                record = next(r for r in self.records if r["sha256"] == sha)
                failed = record["version"] in self.fail_install
                self.present(record,"iF " if failed else "ii ")
                if failed:return 1,b"qualified fixture failure"
            return 0,b""
        if "--configure" in args:
            if self.fail_configure:return 1,b"fixture configure fails"
            self.states[args[-1]]["state"] = "ii ";return 0,b""
        if "--remove" in args:
            self.states.pop(args[-1],None)
            for p in (self.root / "var/lib/dpkg/info").glob(args[-1]+".*"):p.unlink()
            return 0,b""
        if "--triggers-only" in args:self.states[args[-1]]["state"] = "ii ";return 0,b""
        raise AssertionError(args)


@unittest.skipUnless(shutil.which("dpkg-deb") and shutil.which("dpkg"), "real dpkg archive/version tools required")
class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = tempfile.TemporaryDirectory()
        directory = Path(cls.cache.name)
        cls.debs = {v:create_deb(directory,v) for v in ["0.1.6-1","0.1.7-1"]}
        cls.catalog = g.make_catalog(cls.debs.values(),host_run)

    @classmethod
    def tearDownClass(cls):cls.cache.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = FakePackages(self.root,self.catalog["packages"],self.debs)
        self.vendor = b"pinned vendor fixture"
        self.vendor_patch = patch.object(g,"VENDOR_SHA",g.digest(self.vendor));self.vendor_patch.start();self.addCleanup(self.vendor_patch.stop)
        self.policy = {"schema":1,"store_source":g.STORE_COMMIT,"vendor_sha256":g.VENDOR_SHA,"dependencies":[]}
        files = {}
        for name in [g.PUBLIC,g.ORIGINAL,g.HELPER,g.MODULE,g.POLICY,g.CATALOG]:
            path = self.root / name.lstrip("/");path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(self.vendor if name == g.ORIGINAL else name.encode());path.chmod(0o644)
            files[name] = g.digest(path.read_bytes())
        self.baseline = {"schema":1,"complete":True,"packages":{n:"1" for n in ["applaunch","tyrell-zero-session","tyrell-zero-store"]},"files":files,"triggers":{}}
        self.write_baseline()
        self.guard = g.Guard(g.Root(self.root,os.getuid()),self.fake,self.policy,self.catalog)
        self.directory = self.guard.root.mkdir(g.STATE)

    def write_baseline(self):
        (self.root / g.INVARIANTS.lstrip("/")).write_text(json.dumps(self.baseline))

    def record(self,version):return next(r for r in self.catalog["packages"] if r["version"] == version)

    def request(self,version="0.1.6-1",action="install",tx="test-1"):
        current = self.guard.installed("tyrell-video")
        record = self.record(version)
        pending = {"schema_version":2,"transaction_id":tx,"action":action,"package":"tyrell-video",
                   "helper_completed":False,"previously_installed":current["installed"],"previous_version":current["version"],
                   "expected_package_version":version,"expected_md5":record["md5"],"deb_path":str(self.debs[version]),
                   "rollback_deb_path":str(self.debs.get(current["version"],""))}
        args = argparse.Namespace(action="uninstall" if action=="uninstall" else "install", value="tyrell-video" if action=="uninstall" else pending["deb_path"],
                                  transaction=tx, reinstall=action=="reinstall", force_overwrite=False,desktop="",executables=[])
        return pending,args

    def execute(self,pending,args):return self.guard.operate(pending,args,self.directory,os.getuid())

    def repair(self,pending,args):
        pending["repair_requested"] = True;pending["helper_completed"] = False
        args.action="repair";args.value=pending["package"];args.reinstall=False
        return self.execute(pending,args)

    def test_real_install_reinstall_upgrade_downgrade_remove_protocol(self):
        for version,action,tx in [("0.1.6-1","install","a"),("0.1.6-1","reinstall","b"),("0.1.7-1","upgrade","c"),("0.1.6-1","upgrade","d"),("0.1.6-1","uninstall","e")]:
            pending,args = self.request(version,action,tx)
            self.assertTrue(self.execute(pending,args)["helper_completed"])
        self.assertFalse(self.guard.installed("tyrell-video")["installed"])
        commands = [" ".join(c) for c in self.fake.calls]
        self.assertFalse(any("--configure -a" in c or "--pending" in c or "--force-all" in c for c in commands))

    def test_upgrade_failure_uses_root_frozen_previous_bytes(self):
        self.fake.present(self.record("0.1.6-1"))
        pending,args = self.request("0.1.7-1","upgrade")
        self.fake.fail_install.add("0.1.7-1")
        with self.assertRaisesRegex(g.GuardError,"installation failed"):self.execute(pending,args)
        self.fake.fail_configure = True
        # Delete a caller cache copy: repair must use the root-frozen snapshot.
        pending["rollback_deb_path"] = "/does/not/exist"
        self.assertEqual(self.repair(pending,args)["repair_outcome"],"restored")
        self.assertEqual(self.guard.installed("tyrell-video")["version"],"0.1.6-1")

    def test_completed_uninstall_cannot_be_replayed_against_new_version(self):
        self.fake.present(self.record("0.1.6-1"))
        pending,args = self.request("0.1.6-1","uninstall")
        self.execute(pending,args)
        self.fake.present(self.record("0.1.7-1"));pending["helper_completed"] = False
        with self.assertRaisesRegex(g.GuardError,"already executed"):self.execute(pending,args)
        with self.assertRaisesRegex(g.GuardError,"no longer matches"):self.repair(pending,args)
        self.assertEqual(self.guard.installed("tyrell-video")["version"],"0.1.7-1")

    def test_unfinished_journal_cannot_be_superseded_then_replayed(self):
        self.fake.present(self.record("0.1.6-1"))
        interrupted,args = self.request("0.1.6-1","uninstall","interrupted-remove")
        # Power loss after durable authorization but before the remove command.
        self.guard.journal(interrupted,self.directory,self.guard.installed("tyrell-video"))
        newer,new_args = self.request("0.1.7-1","upgrade","later-upgrade")
        before = len([c for c in self.fake.calls if "--install" in c or "--remove" in c])
        with self.assertRaisesRegex(g.GuardError,"unfinished package transaction"):
            self.execute(newer,new_args)
        self.assertEqual(before,len([c for c in self.fake.calls if "--install" in c or "--remove" in c]))
        self.assertEqual(self.guard.installed("tyrell-video")["version"],"0.1.6-1")
        self.repair(interrupted,args)
        self.execute(newer,new_args)
        self.assertEqual(self.guard.installed("tyrell-video")["version"],"0.1.7-1")
        with self.assertRaisesRegex(g.GuardError,"no longer matches"):
            self.repair(interrupted,args)
        self.assertEqual(self.guard.installed("tyrell-video")["version"],"0.1.7-1")

    def test_completed_journal_reconciles_crash_without_reexecuting(self):
        pending,args=self.request();self.execute(pending,args)
        before=len([c for c in self.fake.calls if "--install" in c])
        result=self.repair(pending,args)
        self.assertEqual(result["repair_outcome"],"completed")
        self.assertEqual(before,len([c for c in self.fake.calls if "--install" in c]))

    def test_failed_first_install_configure_repair(self):
        pending,args=self.request();self.fake.fail_install.add("0.1.6-1")
        with self.assertRaises(g.GuardError):self.execute(pending,args)
        self.assertEqual(self.repair(pending,args)["repair_outcome"],"completed")

    def test_failed_first_install_remove_repair(self):
        pending,args=self.request();self.fake.fail_install.add("0.1.6-1");self.fake.fail_configure=True
        with self.assertRaises(g.GuardError):self.execute(pending,args)
        self.assertEqual(self.repair(pending,args)["repair_outcome"],"rolled_back")

    def test_missing_previous_bytes_rejects_upgrade_before_mutation(self):
        self.fake.present(self.record("0.1.6-1"));pending,args=self.request("0.1.7-1","upgrade")
        pending["rollback_deb_path"]=""
        with self.assertRaisesRegex(g.GuardError,"previous package bytes"):self.execute(pending,args)
        self.assertFalse(any("--install" in c for c in self.fake.calls))

    def test_immutable_staging_and_unknown_bytes(self):
        source=self.root/"user.deb";source.write_bytes(self.debs["0.1.6-1"].read_bytes())
        frozen,_=self.guard.stage(source,self.directory)
        source.write_bytes(b"mutated")
        self.assertEqual(g.digest(frozen.read_bytes()),self.record("0.1.6-1")["sha256"])
        with self.assertRaisesRegex(g.GuardError,"not qualified"):self.guard.stage(source,self.directory)

    def test_fifo_package_rejects_without_blocking(self):
        fifo=self.root/"fifo";os.mkfifo(fifo)
        with self.assertRaisesRegex(g.GuardError,"invalid package input"):self.guard.stage(fifo,self.directory)

    def test_known_version_and_real_dependency_comparison(self):
        self.assertTrue(self.guard.satisfied("libc6 (>= 2.38), absent | libc6 (<< 3.0)"))
        self.assertFalse(self.guard.satisfied("libc6 (>= 3.0)"))
        self.assertFalse(self.guard.satisfied("absent"))
        self.fake.states["provider"]={"state":"ii ","version":"4","arch":"arm64","multi":"allowed","provides":"virtual-api (= 2.1)"}
        self.assertTrue(self.guard.satisfied("virtual-api (>= 2.0), provider:any"))
        self.assertFalse(self.guard.satisfied("virtual-api (>= 3.0)"))
        with self.assertRaisesRegex(g.GuardError,"syntax"):self.guard.satisfied("libc6 [arm64]")

    def test_missing_and_base_changing_apt_closure_rejected(self):
        self.fake.states.pop("libc6")
        staged,_=self.guard.stage(self.debs["0.1.6-1"],self.directory)
        for output,message in [(b"Inst libc6 (2.41 stable [arm64])\n","changes the base"),
                               (b"Remv openssh-server [1]\n","removes packages"),
                               (b"Inst missing-lib (1 stable [arm64])\n","frozen dependency"),
                               (b"Inst tyrell-video (9 stable [arm64])\n","substituted target")]:
            self.fake.apt=output
            with self.assertRaisesRegex(g.GuardError,message):self.guard.dependencies(staged)

    def test_current_dependency_version_satisfied_avoids_apt(self):
        staged,_=self.guard.stage(self.debs["0.1.6-1"],self.directory)
        self.assertEqual(self.guard.dependencies(staged),[])
        self.assertFalse(any(Path(c[0]).name=="apt-get" for c in self.fake.calls))

    def test_script_drift_and_unrelated_partial_package_rejected(self):
        self.fake.present(self.record("0.1.6-1"))
        script=self.root/"var/lib/dpkg/info/tyrell-video.postinst";script.write_text("evil")
        pending,args=self.request("0.1.7-1","upgrade")
        with self.assertRaisesRegex(g.GuardError,"script is not qualified"):self.execute(pending,args)
        self.fake.present(self.record("0.1.6-1"));self.fake.audit_override=b"Broken:\n openssh-server pending\n"
        with self.assertRaisesRegex(g.GuardError,"unrelated pending"):self.execute(pending,args)

    def test_protected_payload_normalization_and_symlink_alias(self):
        for value in ["etc/systemd/system/native.service","usr/share/tyrell-zero/store-policy.json","usr/lib/systemd/system/tyrell-zero-session.service"]:
            record=copy.deepcopy(self.record("0.1.6-1"));record["files"]={value:"0"*64}
            with self.assertRaisesRegex(g.GuardError,"protected base"):self.guard.validate_payload(record)
        (self.root/"usr/bin").mkdir(parents=True,exist_ok=True)
        (self.root/"usr/bin/tyrell-video").symlink_to(self.root/g.POLICY.lstrip("/"))
        with self.assertRaises(g.GuardError):self.guard.validate_payload(self.record("0.1.6-1"))
        for name in ["../etc/shadow","/etc/shadow"]:
            with self.assertRaises(g.GuardError):g.archive_name(name)

    def test_base_invariant_drift_fails_before_dpkg(self):
        (self.root/g.MODULE.lstrip("/")).write_text("changed")
        pending,args=self.request()
        with self.assertRaisesRegex(g.GuardError,"base file identity"):self.execute(pending,args)
        self.assertFalse(any("--install" in c for c in self.fake.calls))

    def test_empty_invariant_snapshot_rejected(self):
        self.baseline["packages"]={};self.write_baseline()
        with self.assertRaisesRegex(g.GuardError,"omit required"):self.guard.invariants()

    def test_protected_directory_mode_and_empty_directory_rejected(self):
        record=copy.deepcopy(self.record("0.1.6-1"));record["files"]={}
        record["directories"]={"etc/systemd/system/empty":0o755}
        with self.assertRaisesRegex(g.GuardError,"protected base"):self.guard.validate_payload(record)
        record["directories"]={"usr":0o700}
        with self.assertRaisesRegex(g.GuardError,"directory identity"):self.guard.validate_payload(record)

    def test_pending_awaited_trigger_does_not_run_global_recovery(self):
        self.fake.states["tyrell-video"]={"state":"iW ","version":"0.1.6-1","arch":"arm64","multi":"","provides":""}
        self.guard.settle_triggers()
        self.assertFalse(any("--triggers-only" in c for c in self.fake.calls))

    def test_unqualified_trigger_rejected(self):
        self.fake.states["unknown-base"]={"state":"it ","version":"1","arch":"arm64","multi":"","provides":""}
        with self.assertRaisesRegex(g.GuardError,"qualified base maintenance"):self.guard.settle_triggers()

    def test_qualified_named_trigger_checks_script_identity(self):
        self.fake.states["hicolor-icon-theme"]={"state":"it ","version":"1","arch":"all","multi":"","provides":""}
        self.baseline["triggers"]={"hicolor-icon-theme":{"version":"1","scripts":{}}};self.write_baseline()
        self.guard.settle_triggers()
        self.assertTrue(any(c[-2:]==["--triggers-only","hicolor-icon-theme"] for c in self.fake.calls))
        self.fake.states["hicolor-icon-theme"]["state"]="it "
        (self.root/"var/lib/dpkg/info").mkdir(parents=True,exist_ok=True)
        (self.root/"var/lib/dpkg/info/hicolor-icon-theme.postinst").write_text("unqualified")
        with self.assertRaisesRegex(g.GuardError,"trigger metadata"):self.guard.settle_triggers()

    def test_native_uninstall_pending_omits_target_version(self):
        self.fake.present(self.record("0.1.6-1"));pending,args=self.request(action="uninstall")
        for key in ["expected_package_version","expected_md5","deb_path"]:pending.pop(key)
        self.execute(pending,args)
        self.assertEqual(self.repair(pending,args)["repair_outcome"],"completed")

    def test_unqualified_root_protocol_rejected(self):
        pending,args=self.request();args.force_overwrite=True
        with self.assertRaisesRegex(g.GuardError,"force-overwrite"):self.execute(pending,args)
        args.force_overwrite=False;args.executables=["/bin/sh"]
        with self.assertRaisesRegex(g.GuardError,"executable hint"):self.execute(pending,args)
        args.executables=[];args.transaction="other"
        with self.assertRaisesRegex(g.GuardError,"does not match"):self.execute(pending,args)

    def test_repair_requires_prior_root_journal(self):
        pending,args=self.request()
        with self.assertRaisesRegex(g.GuardError,"no authenticated"):self.repair(pending,args)

    def test_unknown_future_version_rejected(self):
        with self.assertRaisesRegex(g.GuardError,"not qualified"):self.guard.record("tyrell-video","0.1.8-1")
        for package in ["linux-image-rpi-v8","raspberrypi-sys-mods","tyrell-zero-store"]:
            with self.assertRaises(g.GuardError):self.guard.record(package,"1")


class ProductionContract(unittest.TestCase):
    def test_catalog_exact_versions_and_script_identities(self):
        catalog=json.loads((SOURCE/"config/store-catalog.json").read_text())
        self.assertEqual({(x["package"],x["version"]) for x in catalog["packages"]},
                         {("tyrell-status","0.1.0-1"),("tyrell-video","0.1.6-1"),("tyrell-video","0.1.7-1")})
        self.assertEqual(catalog["packages"][0]["sha256"],"15471a8f605e826d6f81daf3260602042b70b9c19330cb03b0bf839039533b2b")
        video=[x for x in catalog["packages"] if x["package"]=="tyrell-video"]
        self.assertEqual(video[0]["scripts"],video[1]["scripts"])
        self.assertEqual(set(video[0]["scripts"]),{"postinst","prerm","postrm"})

    def test_runtime_has_no_root_policy_environment_override(self):
        self.assertEqual(json.loads((SOURCE/"config/store-policy.json").read_text())["vendor_sha256"],g.VENDOR_SHA)
        self.assertNotIn("M5APPSTORE_STATE_DIR",(SOURCE/"guard.py").read_text())
        with self.assertRaises(SystemExit):g.parse_args(["--root","/tmp"])


@unittest.skipUnless(shutil.which("dpkg-divert"), "real dpkg-divert required")
class OfflineDiversionTests(unittest.TestCase):
    def test_real_registered_diversion_prepare_idempotence_restore(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location("activation",SOURCE/"activate-image.py")
        activation=importlib.util.module_from_spec(spec);spec.loader.exec_module(activation)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/"var/lib/dpkg").mkdir(parents=True)
            (root/"var/lib/dpkg/status").write_text("")
            public=root/g.PUBLIC.lstrip("/");public.parent.mkdir(parents=True);public.write_bytes(b"vendor fixture")
            def execute(root,*args):
                rc,out=host_run(list(args))
                if rc:raise g.GuardError(out.decode())
                return out.decode().strip()
            with patch.object(activation,"VENDOR_SHA",g.digest(public.read_bytes())):
                activation.diversion(root,"prepare",execute)
                self.assertEqual(execute(root,"/usr/bin/dpkg-divert","--root="+str(root),"--admindir="+str(root/"var/lib/dpkg"),"--listpackage",g.PUBLIC),"tyrell-zero-store")
                activation.diversion(root,"prepare",execute)
                public.write_text("owned dispatcher")
                with self.assertRaisesRegex(g.GuardError,"remove/purge"):activation.diversion(root,"restore",execute)
                public.unlink();activation.diversion(root,"restore",execute)
                self.assertEqual(public.read_bytes(),b"vendor fixture")
                self.assertEqual(execute(root,"/usr/bin/dpkg-divert","--root="+str(root),"--admindir="+str(root/"var/lib/dpkg"),"--listpackage",g.PUBLIC),"")
        with self.assertRaises(g.GuardError):activation.image_root("/")


if __name__=="__main__":unittest.main()
