"""Captured bytes, attempt identity and terminal-stage retry boundaries."""
import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shlex
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
zero=runpy.run_path(str(ROOT/'image/zero_pipeline.py'))
resume=runpy.run_path(str(ROOT/'image/zero_resume.py'))
legacy=runpy.run_path(str(ROOT/'image/build-image.py'))
prepare=runpy.run_path(str(ROOT/'image/prepare-zero.py'))
ID='20261123T180000000000Z-1234abcd'


def sha(data):return hashlib.sha256(data).hexdigest()


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.files={'image/profiles/tyrell-zero.json':b'profile',
                    'apt-repository/apt-lock.json':b'apt'}
        for name in zero['LOCKS']:
            self.files['locks/'+name]=b'apt' if name=='tyrell-zero-apt.json' else name.encode()
        identities={name:{'sha256':sha(data),'bytes':len(data),'mode':0o644} for name,data in self.files.items()}
        self.metadata={'job_id':ID,'profile_id':'tyrell-zero','build_mode':'zero-native','files':identities,
                       'capture_sha256':sha(json.dumps(identities,sort_keys=True).encode()),
                       'profile_sha256':sha(b'profile'),
                       'locks_sha256':{name:sha(self.files['locks/'+name]) for name in zero['LOCKS']}}

    def archive(self,*,replace=None,extra=None):
        path=self.root/'inputs.tar'
        rows={**self.files,'image-metadata.json':json.dumps(self.metadata).encode()}
        rows.update(replace or {})
        with tarfile.open(path,'w') as out:
            for name,data in rows.items():
                info=tarfile.TarInfo(name);info.mode=0o644;info.size=len(data)
                out.addfile(info,io.BytesIO(data))
            if extra is not None:out.addfile(extra,io.BytesIO(b'x') if extra.isfile() else None)
        return path

    def test_exact_capture_is_accepted(self):
        self.assertEqual(zero['verify_bundle'](self.archive()),self.metadata)

    def test_changed_package_or_recipe_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'Changed captured'):
            zero['verify_bundle'](self.archive(replace={'apt-repository/apt-lock.json':b'bad'}))

    def test_undeclared_file_rejected(self):
        item=tarfile.TarInfo('private');item.size=1
        with self.assertRaisesRegex(ValueError,'inventory'):
            zero['verify_bundle'](self.archive(extra=item))

    def test_duplicate_and_links_and_traversal_rejected(self):
        for name,kind in [('locks/tyrell-zero-apt.json',tarfile.REGTYPE),
                          ('link',tarfile.SYMTYPE),('../escape',tarfile.REGTYPE)]:
            with self.subTest(name=name):
                item=tarfile.TarInfo(name);item.type=kind;item.size=1 if kind==tarfile.REGTYPE else 0
                item.linkname='/etc/passwd'
                with self.assertRaises(ValueError):zero['verify_bundle'](self.archive(extra=item))

    def test_rebound_profile_hash_rejected(self):
        self.metadata['profile_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'profile'):
            zero['verify_bundle'](self.archive())

    def test_rebound_repository_lock_rejected(self):
        self.metadata['locks_sha256']['tyrell-zero-apt.json']='0'*64
        with self.assertRaisesRegex(ValueError,'lock'):
            zero['verify_bundle'](self.archive())

    def test_json_duplicate_fields_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            zero['read_json']('{"job_id":"first","job_id":"second"}')

    def test_credentials_cannot_be_captured_as_recipe_or_assets(self):
        for name in ['image/.env','assets/.env.private','image/contracts/.env/config.json']:
            with self.assertRaises(ValueError):zero['relative'](name)

    def test_export_checks_artifacts_against_durable_guest_manifest(self):
        guest={'verification':{'status':'passed'},'sha256':{'image.img.xz':'a'*64}}
        path=self.root/'guest-image-manifest.json';path.write_text(json.dumps(guest))
        zero['verify_guest_manifest'](self.root,zero['digest'](path),{'image.img.xz':'a'*64})
        with self.assertRaisesRegex(ValueError,'verified guest artifact'):
            zero['verify_guest_manifest'](self.root,zero['digest'](path),{'image.img.xz':'b'*64})


class NativeSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.vendor=Path(self.tmp.name)/'vendor';self.vendor.mkdir()
        self.original='#!/bin/bash\n'+prepare['VENDOR_RENAME_GUARD']+'\nprintf "accepted\\n"\n'
        (self.vendor/'build.sh').write_text(self.original)
        (self.vendor/'tyrell-inputs.conf').write_text('')
        recipe=(ROOT/'builder/build-zero-image.sh').read_text()
        self.config=recipe.split('<<CONFIG\n',1)[1].split('\nCONFIG',1)[0]

    def execute_guard(self, config=None):
        script='vendor='+shlex.quote(str(self.vendor))+'\n'+(self.config if config is None else config)
        script+='\n'+(self.vendor/'build.sh').read_text()
        return subprocess.run(['/bin/bash','-c',script],text=True,capture_output=True)

    def test_actual_zero_recipe_skips_vendor_rename_with_locked_factory_account(self):
        self.assertNotEqual(self.execute_guard().returncode,0)
        prepare['configure_native_setup'](self.vendor)
        self.assertEqual(self.execute_guard().returncode,0)
        for old,new in [('TYRELL_ZERO_NATIVE_SETUP=1','TYRELL_ZERO_NATIVE_SETUP=0'),
                        ('ENABLE_SSH=0','ENABLE_SSH=1'),
                        ('PASSWORDLESS_SUDO=0','PASSWORDLESS_SUDO=1'),
                        ('FIRST_USER_NAME=pi','FIRST_USER_NAME=other')]:
            with self.subTest(config=new):
                self.assertNotEqual(self.execute_guard(self.config.replace(old,new)).returncode,0)
        result=self.execute_guard(self.config+'\nprintf "identity:%s:%s\\n" "$DISABLE_FIRST_BOOT_USER_RENAME" "$FIRST_USER_PASS"')
        self.assertIn('identity:1:\n',result.stdout)

    def test_changed_vendor_guard_is_rejected_without_rewriting_it(self):
        path=self.vendor/'build.sh'
        changed=self.original.replace('FIRST_USER_PASS needs to be set','different vendor policy')
        path.write_text(changed)
        with self.assertRaisesRegex(ValueError,'guard differs'):
            prepare['configure_native_setup'](self.vendor)
        self.assertEqual(path.read_text(),changed)

    def generated_install(self):
        for directory in ('stage4/99-tyrell','stage2','export-image/02-set-sources'):
            (self.vendor/directory).mkdir(parents=True,exist_ok=True)
        (self.vendor/'stage2/SKIP_IMAGES').touch()
        (self.vendor/'tyrell-prepared.json').write_text('{"prepared_files_sha256":{}}')
        metadata={'profile_id':'tyrell-zero','stages':['stage0','stage1','stage2'],
                  'selected_power_preset':'baseline','output':{},'profile_sha256':'a'*64,'locks_sha256':{}}
        path=self.vendor/'metadata.json';path.write_text(json.dumps(metadata))
        dependencies=SimpleNamespace(run_path=lambda name: {'configure_vendor':lambda *_args:None}
            if str(name).endswith('zero-lock.py') else {'digest':zero['digest']})
        with patch.dict(prepare['prepare'].__globals__,runpy=dependencies,
                        subprocess=SimpleNamespace(run=lambda *_a,**_kw:None)):
            report=prepare['prepare'](self.vendor,self.vendor/'assets',path)
        self.assertEqual(report['prepared_files_sha256']['build.sh'],zero['digest'](self.vendor/'build.sh'))
        stage=(self.vendor/'stage2/99-tyrell/00-run.sh').read_text()
        return stage.split("on_chroot <<'TYRELL_ZERO_INSTALL'\n",1)[1].split('\nTYRELL_ZERO_INSTALL',1)[0]

    def test_generated_install_reasserts_root_lock_and_fails_before_activation_on_error(self):
        install=self.generated_install()
        stubs='apt-get(){ echo "apt:$*"; }; dpkg(){ echo "dpkg:$*"; }; rm(){ echo "cleanup:$*"; };\n'
        for status in (0,42):
            script=stubs+'passwd(){ echo "passwd:$*"; return '+str(status)+'; };\n'+install+'\necho finished\n'
            result=subprocess.run(['/bin/bash','-c',script],text=True,capture_output=True)
            self.assertIn('passwd:-l root\n',result.stdout)
            if status==0:
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertLess(result.stdout.index('passwd:-l root'),result.stdout.index('dpkg:--audit'))
                self.assertIn('finished',result.stdout)
            else:
                self.assertNotEqual(result.returncode,0)
                self.assertNotIn('dpkg:--audit',result.stdout)
                self.assertNotIn('finished',result.stdout)


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.job={'job_id':ID,'bundle_sha256':'a'*64,'active_attempt':2,'attempts':[]}
        self.job['attempts']=[zero['attempt_record'](self.job,1,'fresh'),zero['attempt_record'](self.job,2,'stage2')]
        self.attempt=self.job['attempts'][1]
        self.result={key:self.attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
        self.result.update(exit_status=0,manifest_sha256='b'*64)
        self.unit={'ActiveState':'inactive','Environment':''}
        self.common={'unit_state':lambda:self.unit,'running':lambda value:value['ActiveState']=='active'}
        def guest(*_a,**_kw):return SimpleNamespace(stdout=json.dumps(self.result))
        self.stub={'guest':guest}

    def outcome(self):
        with patch.dict(zero['outcome'].__globals__,api=lambda:self.stub):
            return zero['outcome'](self.job,self.attempt,self.common)

    def test_current_attempt_and_explicit_old_attempt(self):
        self.assertEqual(zero['selected'](self.job)['attempt_id'],2)
        self.assertEqual(zero['selected'](self.job,1)['attempt_id'],1)
        self.assertEqual(self.outcome()['state'],'succeeded')

    def test_previous_success_does_not_certify_retry(self):
        self.result['attempt_id']=1
        with self.assertRaisesRegex(ValueError,'Stale'):self.outcome()

    def test_changed_input_result_rejected(self):
        self.result['bundle_sha256']='c'*64
        with self.assertRaises(ValueError):self.outcome()

    def test_success_needs_verified_output_manifest(self):
        self.result.pop('manifest_sha256')
        with self.assertRaisesRegex(ValueError,'output identity'):self.outcome()

    def test_running_identity_wins_over_early_result(self):
        self.unit={'ActiveState':'active','Environment':f'TYRELL_IMAGE_JOB_ID={ID} TYRELL_IMAGE_ATTEMPT_ID=2 TYRELL_IMAGE_INPUT_SHA256='+ 'a'*64}
        self.assertEqual(self.outcome()['state'],'running')

    def test_unrelated_service_cannot_certify_missing_result(self):
        self.result=None;self.unit={'ActiveState':'active','Environment':'TYRELL_IMAGE_JOB_ID=other'}
        with self.assertRaisesRegex(ValueError,'authoritative'):self.outcome()

    def test_failed_launch_is_durable_when_no_unit_started(self):
        self.unit={'LoadState':'not-found','ActiveState':'inactive','Environment':''}
        writes=[]
        b={'guest':lambda *args,**kwargs:writes.append(args)}
        self.assertFalse(zero['reconcile_launch'](self.job,self.attempt,self.common,b))
        failure=json.loads(writes[0][-1])
        self.assertEqual(failure['attempt_id'],2)
        self.assertEqual(failure['bundle_sha256'],'a'*64)
        self.assertEqual(failure['exit_status'],125)
        self.assertTrue(failure['launch_failed'])

    def test_uncertain_launch_reattaches_matching_unit_without_poisoning_result(self):
        self.unit={'LoadState':'loaded','ActiveState':'active','Environment':f'TYRELL_IMAGE_JOB_ID={ID} TYRELL_IMAGE_ATTEMPT_ID=2 TYRELL_IMAGE_INPUT_SHA256='+ 'a'*64}
        def forbidden(*a,**kw):raise AssertionError('Must not overwrite a potentially live result')
        self.assertTrue(zero['reconcile_launch'](self.job,self.attempt,self.common,{'guest':forbidden}))

    def test_unknown_launch_state_cannot_authorize_retry(self):
        self.unit={'LoadState':'error','ActiveState':'unknown','Environment':''}
        with self.assertRaisesRegex(ValueError,'reattach'):
            zero['reconcile_launch'](self.job,self.attempt,self.common,{'guest':lambda *a:None})


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.job={'job_id':ID,'bundle_sha256':sha(b'captured archive')}
        self.attempt=zero['attempt_record'](self.job,2,'stage2')
        identity={key:self.attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
        (self.root/'attempt.json').write_text(json.dumps(identity))
        (self.root/'image.img.xz').write_bytes(b'verified image')
        guest={'verification':{'status':'passed'},'inputs':{'profile_id':'tyrell-zero'},
               'sha256':{name:zero['digest'](self.root/name) for name in ['attempt.json','image.img.xz']}}
        (self.root/'guest-image-manifest.json').write_text(json.dumps(guest))
        self.guest_sha=zero['digest'](self.root/'guest-image-manifest.json')
        host=copy.deepcopy(guest)
        host['provenance']={**identity,'input_archive':'image-input.tar','guest_manifest_sha256':self.guest_sha}
        host['sha256']['image-input.tar']=self.job['bundle_sha256']
        (self.root/'image-manifest.json').write_text(json.dumps(host))
        (self.root/'image-input.tar').write_bytes(b'captured archive')
        self.common={'verify_checksums':lambda directory:{p.name:zero['digest'](p) for p in Path(directory).iterdir() if p.is_file()}}

    def validate(self):zero['validate_host_export'](self.root,self.job,self.attempt,self.guest_sha,self.common)
    def test_exact_guest_to_host_augmentation_accepted(self):self.validate()
    def test_changed_host_manifest_rejected_even_with_recomputed_checksums(self):
        path=self.root/'image-manifest.json';doc=json.loads(path.read_text())
        doc['inputs']['profile_id']='other';path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError,'augmentation'):self.validate()
    def test_changed_archived_inputs_rejected_even_with_recomputed_checksums(self):
        (self.root/'image-input.tar').write_bytes(b'changed archive')
        with self.assertRaisesRegex(ValueError,'input archive'):self.validate()


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=(Path(self.tmp.name)/ID).resolve();self.root.mkdir()
        self.metadata={'profile_id':'tyrell-zero','stages':['stage0','stage1','stage2'],
                       'profile_sha256':'a'*64,'capture_sha256':'b'*64}
        self.identity={'job_id':ID,'bundle_sha256':'c'*64,'profile_sha256':'a'*64,'capture_sha256':'b'*64,'config_sha256':sha(b'config')}
        self.write('input/image-metadata.json',self.metadata)
        self.write('work/zero-job-identity.json',self.identity)
        self.write('attempts/0001/result.json',{'job_id':ID,'bundle_sha256':'c'*64,'attempt_id':1,'exit_status':1})
        self.text('work/pi-gen/config','config')
        self.text('work/pi-gen/work/tyrell-zero-trixie-arm64/stage1/rootfs/etc/os-release','VERSION_CODENAME=trixie\n')
        self.text('work/pi-gen/work/tyrell-zero-trixie-arm64/build.log','[12:00:00] End /job/work/pi-gen/stage1\n')

    def text(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
    def write(self,name,value):self.text(name,json.dumps(value))
    def validate(self,**kwargs):return resume['validate'](self.root,'c'*64,1,mounts=kwargs.pop('mounts',[]),unit=kwargs.pop('unit',{'ActiveState':'failed'}),**kwargs)

    def test_terminal_failed_stage2_with_completed_stage1_is_valid(self):self.assertEqual(self.validate(),self.identity)
    def test_live_service_refused_even_with_old_failed_result(self):
        with self.assertRaisesRegex(ValueError,'Live'):self.validate(unit={'ActiveState':'active'})
    def test_leftover_mount_refused(self):
        with self.assertRaisesRegex(ValueError,'mount'):self.validate(mounts=[str(self.root/'work/pi-gen/stage2/rootfs/proc')])
    def test_changed_configuration_refused(self):
        self.text('work/pi-gen/config','changed')
        with self.assertRaisesRegex(ValueError,'configuration'):self.validate()
    def test_missing_stage1_completion_refused(self):
        self.text('work/pi-gen/work/tyrell-zero-trixie-arm64/build.log','[12:00:00] Begin /job/work/pi-gen/stage1\n')
        with self.assertRaisesRegex(ValueError,'completion'):self.validate()
    def test_previous_success_refused(self):
        self.write('attempts/0001/result.json',{'job_id':ID,'bundle_sha256':'c'*64,'attempt_id':1,'exit_status':0})
        with self.assertRaisesRegex(ValueError,'Successful'):self.validate()
    def test_missing_result_needs_matching_authoritative_failed_unit(self):
        (self.root/'attempts/0001/result.json').unlink()
        with self.assertRaisesRegex(ValueError,'authoritative'):self.validate()
        unit={'ActiveState':'failed','Environment':f'TYRELL_IMAGE_JOB_ID={ID} TYRELL_IMAGE_ATTEMPT_ID=1 TYRELL_IMAGE_INPUT_SHA256='+ 'c'*64}
        self.assertEqual(self.validate(unit=unit),self.identity)
    def test_cross_profile_refused(self):
        self.metadata['profile_id']='desktop';self.write('input/image-metadata.json',self.metadata)
        with self.assertRaisesRegex(ValueError,'Cross-profile'):self.validate()

    def test_authorized_retry_can_validate_only_its_own_running_unit(self):
        record={**self.identity,'previous_attempt':1,'authorized_attempt':2}
        self.write('resume-authorized-0002.json',record)
        (self.root/'resume-authorized-0002.json').chmod(0o600)
        unit={'ActiveState':'active','Environment':f'TYRELL_IMAGE_JOB_ID={ID} TYRELL_IMAGE_ATTEMPT_ID=2 TYRELL_IMAGE_INPUT_SHA256='+ 'c'*64}
        self.assertEqual(self.validate(unit=unit,authorized_attempt=2,owner=os.getuid()),self.identity)
        unit['Environment']=unit['Environment'].replace('ATTEMPT_ID=2','ATTEMPT_ID=1')
        with self.assertRaisesRegex(ValueError,'Live'):
            self.validate(unit=unit,authorized_attempt=2,owner=os.getuid())


class CliTests(unittest.TestCase):
    def test_prepared_status_never_contacts_guest(self):
        args=SimpleNamespace(action='status',storage=None,job=ID,attempt=None)
        job={'job_id':ID,'active_attempt':0,'bundle_sha256':'a'*64,'attempts':[]}
        def forbidden():raise AssertionError('Prepared status must not contact or start the VM')
        with patch.dict(zero['main'].__globals__,load_job=lambda *_a:job,api=forbidden):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):zero['main'](args,{'storage':lambda **_kw:Path('/unused')})
        self.assertEqual(json.loads(output.getvalue())['state'],'prepared')

    def test_zero_disallows_legacy_profile_combinations(self):
        for extra in [['--stock-lock','stock.json'],['--discovery'],['--resume-from','stage4']]:
            with self.assertRaises(SystemExit):legacy['parse_args'](['--profile','image/profiles/tyrell-zero.json',*extra])
    def test_resume_requires_existing_job_and_stage2(self):
        args=legacy['parse_args'](['resume','--job',ID,'--from','stage2'])
        self.assertEqual(args.action,'resume')
        for extra in [['--profile','profile.json'],['--discovery'],['--attempt','1']]:
            with self.assertRaises(SystemExit):legacy['parse_args'](['resume','--job',ID,'--from','stage2',*extra])


if __name__=='__main__':unittest.main()
