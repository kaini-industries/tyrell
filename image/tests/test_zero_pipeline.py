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


class VendorCacheTests(unittest.TestCase):
    def test_zero_cache_is_commit_keyed_and_preserves_legacy_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory);legacy=target/'sources/pi-gen';legacy.mkdir(parents=True)
            sentinel=legacy/'baseline';sentinel.write_bytes(b'retained September checkout')
            commit='a'*40;vendor=target/'sources'/('pi-gen-'+commit);vendor.mkdir();(vendor/'.git').mkdir()
            with patch.dict(zero['vendor_source'].__globals__,subprocess=SimpleNamespace(
                    check_output=lambda args,**kwargs:commit+'\n' if args[-1]=='HEAD' else '',
                    run=lambda *_a,**_kw: self.fail('existing cache must not be changed'))):
                self.assertEqual(zero['vendor_source'](target,commit),vendor)
            self.assertEqual(sentinel.read_bytes(),b'retained September checkout')

    def test_dirty_wrong_commit_and_redirected_cache_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory);commit='a'*40;vendor=target/'sources'/('pi-gen-'+commit)
            vendor.mkdir(parents=True);(vendor/'.git').mkdir()
            for head,dirty in [(commit,' M build.sh\n'),('b'*40,'')]:
                with self.subTest(head=head,dirty=dirty),patch.dict(zero['vendor_source'].__globals__,
                        subprocess=SimpleNamespace(check_output=lambda args,**kwargs:head+'\n' if args[-1]=='HEAD' else dirty)):
                    with self.assertRaisesRegex(ValueError,'pristine'):zero['vendor_source'](target,commit)
            (vendor/'.git').rmdir();vendor.rmdir();vendor.symlink_to(target/'other')
            with self.assertRaisesRegex(ValueError,'symlinked'):zero['vendor_source'](target,commit)
            with self.assertRaisesRegex(ValueError,'full pinned commit'):zero['vendor_source'](target,'main')


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
        # This test executes only the generated passwd/audit shell fragment,
        # not a captured runtime dependency transaction. Keep its synthetic
        # closure coherent while the real profile/locks are recaptured.
        recipe=self.vendor.parent/'recipe';(recipe/'locks').mkdir(parents=True)
        (recipe/'image/profiles').mkdir(parents=True)
        profile=json.loads((ROOT/'image/profiles/tyrell-zero.json').read_text())
        (recipe/'image/profiles/tyrell-zero.json').write_text(json.dumps(profile))
        apt={'installed':[[name,'fixture-version','arm64'] for name in
                         sorted({name for group in profile['runtime_roots'].values() for name in group})]}
        (recipe/'locks/tyrell-zero-apt.json').write_text(json.dumps(apt))
        (recipe/'locks/tyrell-zero-apps.json').write_bytes((ROOT/'locks/tyrell-zero-apps.json').read_bytes())
        dependencies=SimpleNamespace(run_path=lambda name: {'configure_vendor':lambda *_args:None}
            if str(name).endswith('zero-lock.py') else {'digest':zero['digest']})
        with patch.dict(prepare['prepare'].__globals__,ROOT=recipe,runpy=dependencies,
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


class LaunchTests(unittest.TestCase):
    def launch_case(self, old, *, fail=None, pointer_error=None, observed=None):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);directory=root/'jobs'/ID;directory.mkdir(parents=True)
            (root/'build').mkdir();(directory/'image-input.tar').write_bytes(b'captured')
            job={'job_id':ID,'bundle_sha256':'a'*64,'guest_input':zero['GUEST_BASE']+'/'+ID+'/input',
                 'active_attempt':0,'attempts':[]}
            state=dict(old);calls=[];result=root/'durable-result.json'
            original_write=zero['write_json']
            def write(path,value):
                if Path(path)==root/'build/image-job.json' and pointer_error:raise pointer_error
                original_write(path,value)
            def guest(*args,**_kwargs):
                calls.append(args)
                if args[:2]==('python3','-c'):
                    return SimpleNamespace(stdout='100000000000')
                # Every guest mutation must follow the durable host attempt.
                self.assertEqual(json.loads((directory/'job.json').read_text())['active_attempt'],1)
                if 'systemctl' in args:
                    action=args[3]
                    if fail==action:raise subprocess.CalledProcessError(1,args)
                    if action=='stop' and state.get('LoadState')=='not-found':
                        raise subprocess.CalledProcessError(5,args)
                    state.update(LoadState='not-found',ActiveState='inactive',Environment='')
                elif 'mkdir' in args and fail=='mkdir':
                    raise OSError('attempt directory preparation failed')
                elif 'systemd-run' in args:
                    if fail=='systemd-run':raise subprocess.TimeoutExpired(args,1)
                    state.update(LoadState='loaded',ActiveState='active',Environment=
                        f'TYRELL_IMAGE_JOB_ID={ID} TYRELL_IMAGE_ATTEMPT_ID=1 TYRELL_IMAGE_INPUT_SHA256='+'a'*64)
                elif args[:4]==('sudo','-n','python3','-c'):
                    original_write(result,json.loads(args[-1]))
                return SimpleNamespace(stdout='')
            common={'assert_idle':lambda:dict(old),'unit_state':lambda:observed if observed is not None else state,
                    'UNIT':'tyrell-image.service','GUEST_LOCK':'/var/tmp/test-image.lock'}
            resources=SimpleNamespace(run_path=lambda _name:{'preflight':lambda *_a,**_kw:None})
            error=None;attempt=None
            with patch.dict(zero['launch'].__globals__,ROOT=root,api=lambda:{'guest':guest},
                            runpy=resources,write_json=write),contextlib.redirect_stdout(io.StringIO()):
                try:attempt=zero['launch'](root,job,common)
                except Exception as caught:error=caught
            return {'error':error,'attempt':attempt,'calls':calls,'job':json.loads((directory/'job.json').read_text()),
                    'result':json.loads(result.read_text()) if result.exists() else None}

    def test_reset_garbage_collects_failed_transient_without_a_following_stop(self):
        case=self.launch_case({'LoadState':'loaded','ActiveState':'failed','Environment':''})
        self.assertIsNone(case['error']);self.assertEqual(case['attempt']['attempt_id'],1)
        self.assertEqual([args[3] for args in case['calls'] if 'systemctl' in args],['reset-failed'])
        self.assertIsNone(case['result'])

    def test_completed_loaded_unit_stops_without_reset(self):
        case=self.launch_case({'LoadState':'loaded','ActiveState':'active','SubState':'exited','Environment':''})
        self.assertIsNone(case['error'])
        self.assertEqual([args[3] for args in case['calls'] if 'systemctl' in args],['stop'])

    def test_cleanup_and_preparation_failures_have_durable_bound_failure(self):
        for action in ('reset-failed','mkdir','systemd-run'):
            with self.subTest(action=action):
                case=self.launch_case({'LoadState':'loaded','ActiveState':'failed','Environment':''},fail=action)
                self.assertIsNotNone(case['error']);self.assertIsNone(case['attempt'])
                self.assertEqual(case['result'],{'job_id':ID,'attempt_id':1,'bundle_sha256':'a'*64,
                    'exit_status':125,'manifest_sha256':None,'launch_failed':True})

    def test_pointer_write_after_attempt_persistence_is_reconciled(self):
        for error in (OSError('pointer write failed'),ValueError('redirected pointer')):
            with self.subTest(error=error):
                case=self.launch_case({'LoadState':'not-found','Environment':''},pointer_error=error)
                self.assertIs(case['error'],error);self.assertEqual(case['job']['active_attempt'],1)
                self.assertEqual(case['result']['exit_status'],125)
                self.assertFalse(any('systemd-run' in args for args in case['calls']))

    def test_unknown_cleanup_state_remains_reattach_only(self):
        case=self.launch_case({'LoadState':'loaded','ActiveState':'failed','Environment':''},fail='reset-failed',
                              observed={'LoadState':'error','ActiveState':'unknown'})
        self.assertIsInstance(case['error'],ValueError)
        self.assertIn('reattach',str(case['error']));self.assertIsNone(case['result'])


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.job={'job_id':ID,'bundle_sha256':sha(b'captured archive')}
        self.attempt=zero['attempt_record'](self.job,2,'stage2')
        identity={key:self.attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
        (self.root/'attempt.json').write_text(json.dumps(identity))
        (self.root/'image.img.xz').write_bytes(b'verified image')
        guest={'verification':{'status':'passed'},'inputs':{'profile_id':'tyrell-zero',
               'build_mode':'zero-native','pi_gen_commit':zero['LEGACY_BASELINE']},
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

    def publish(self, commit):
        guest=zero['read_json']((self.root/'guest-image-manifest.json').read_text())
        guest['inputs']['pi_gen_commit']=commit
        (self.root/'guest-image-manifest.json').write_text(json.dumps(guest))
        guest_sha=zero['digest'](self.root/'guest-image-manifest.json')
        identity={key:self.attempt[key] for key in ('job_id','attempt_id','bundle_sha256')}
        host=copy.deepcopy(guest)
        host['provenance']={**identity,'input_archive':'image-input.tar','guest_manifest_sha256':guest_sha}
        host['sha256']['image-input.tar']=self.job['bundle_sha256']
        (self.root/'image-manifest.json').write_text(json.dumps(host))
        workspace=self.root/'workspace';workspace.mkdir()
        target=self.root/'storage';target.mkdir();(target/'images').mkdir()
        destination=target/'images'/(ID+'-attempt-0002');destination.mkdir()
        for item in list(self.root.iterdir()):
            if item.is_file():item.rename(destination/item.name)
        retained={}
        for name in ('image-artifacts-zero','image-artifacts-zero-nov19',
                     'image-artifacts-control','image-artifacts'):
            original=workspace/('retained-'+name);original.mkdir()
            (original/'sentinel').write_text(name)
            (workspace/name).symlink_to(original,target_is_directory=True)
            retained[name]=original
        common={**self.common,'assert_idle':lambda:None}
        state={'state':'succeeded','result':{'manifest_sha256':guest_sha}}
        with patch.dict(zero['export'].__globals__,ROOT=workspace,api=lambda:{},
                        outcome=lambda *_a:state),contextlib.redirect_stdout(io.StringIO()):
            result=zero['export'](target,self.job,self.attempt,common)
        self.assertEqual(result,destination)
        return workspace,destination,retained

    def test_november_publication_preserves_baseline_control_and_stock_pointers(self):
        workspace,destination,retained=self.publish(zero['NOV19_BASELINE'])
        self.assertEqual((workspace/'image-artifacts-zero-nov19').resolve(),destination)
        for name in ('image-artifacts-zero','image-artifacts-control','image-artifacts'):
            self.assertEqual((workspace/name).resolve(),retained[name])
            self.assertEqual((retained[name]/'sentinel').read_text(),name)

    def test_legacy_captured_job_keeps_its_original_publication_name(self):
        workspace,destination,retained=self.publish(zero['LEGACY_BASELINE'])
        self.assertEqual((workspace/'image-artifacts-zero').resolve(),destination)
        for name in ('image-artifacts-zero-nov19','image-artifacts-control','image-artifacts'):
            self.assertEqual((workspace/name).resolve(),retained[name])

    def test_publication_rejects_unknown_or_non_zero_capture_headers(self):
        for metadata in (None,{}, {'profile_id':'other','build_mode':'zero-native',
                                  'pi_gen_commit':zero['NOV19_BASELINE']},
                         {'profile_id':'tyrell-zero','build_mode':'stock-image',
                          'pi_gen_commit':zero['NOV19_BASELINE']},
                         {'profile_id':'tyrell-zero','build_mode':'zero-native','pi_gen_commit':'a'*40}):
            with self.subTest(metadata=metadata),self.assertRaises(ValueError):
                zero['publication_name'](metadata)


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
