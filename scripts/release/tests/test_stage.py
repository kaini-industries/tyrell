"""Reviewed-selection staging without remote writes, rebuilds or replacement."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import github
import stage

REPO="kaini-industries/tyrell";SOURCE="a"*40


def canonical(value):return (json.dumps(value,sort_keys=True,indent=2)+"\n").encode()
def digest(value):return hashlib.sha256(value).hexdigest()


class StageTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.repo=self.root/'repo';self.repo.mkdir()
        self.inputs=self.root/'inputs';self.hosted={};checks=[]
        for suite in github.SUITES:
            data={'schema':1,'suite':suite,'status':'passed','source':{'commit':SOURCE,'dirty':False}}
            body=canonical(data);self.hosted[suite]=body
            checks.append({'path':f'checks/{suite}/{suite}.json','sha256':digest(body)})
        self.documents=[]
        for role in ('qualification','known-issues','license','recovery','inventory','input-manifest'):
            name='research/candidate-'+role+'.txt';path=self.repo/name;path.parent.mkdir(exist_ok=True);path.write_bytes((role+' reviewed data\n').encode())
            self.documents.append({'path':name,'sha256':github.sha(path),'role':role})
        self.selection={'schema':1,'source_commit':SOURCE,'release_version':'example-dev-1','target':'original-m5',
                        'checks':checks,'documents':self.documents,'packages':[],'image':None}
        self.selection_path='research/reviewed-selection.json';self.save()

    def save(self):
        path=self.repo/self.selection_path;path.write_bytes(canonical(self.selection));self.selection_sha=github.sha(path)

    def download(self,*args,**kwargs):
        self.assertEqual(args[:2],('run','download'));self.assertFalse(kwargs)
        name=args[args.index('--name')+1];suite=next(s for s in github.SUITES if name==f'checks-{s}-{SOURCE}')
        target=Path(args[args.index('--dir')+1]);target.mkdir(parents=True)
        (target/(suite+'.json')).write_bytes(self.hosted[suite]);(target/'retained-evidence.txt').write_text(suite+' evidence')
        return ''

    def invoke(self,**kwargs):
        return stage.stage(REPO,101,SOURCE,self.selection_path,self.selection_sha,self.inputs,repository_root=self.repo,**kwargs)

    def mocks(self):
        return (patch.object(github,'run',return_value={'id':101,'head_sha':SOURCE}),
                patch.object(github,'artifact',return_value={'id':303}),patch.object(github,'gh',side_effect=self.download))

    def test_downloads_all_canonical_artifacts_and_preserves_exact_recipe(self):
        a,b,c=self.mocks()
        with a as run,b as artifact,c as download:result=self.invoke()
        self.assertEqual(run.call_args.args,(REPO,101,'.github/workflows/checks.yml',SOURCE))
        self.assertEqual(artifact.call_count,8);self.assertEqual(download.call_count,8)
        self.assertEqual(result['downloaded_suites'],list(github.SUITES));self.assertFalse(result['promotable'])
        self.assertEqual((self.inputs/'release-selection.json').read_bytes(),(self.repo/self.selection_path).read_bytes())
        for document in self.documents:self.assertEqual(github.sha(self.inputs/document['path']),document['sha256'])

    def test_matching_retained_reports_and_documents_are_reused_without_download(self):
        a,b,c=self.mocks()
        with a,b,c:self.invoke()
        a,b,c=self.mocks()
        with a,b,c as download:result=self.invoke()
        download.assert_not_called();self.assertEqual(result['retained_suites'],list(github.SUITES));self.assertFalse(result['copied_documents'])

    def test_existing_mismatched_reports_are_preserved(self):
        name=self.selection['checks'][0]['path'];path=self.inputs/name;path.parent.mkdir(parents=True);path.write_bytes(b'earlier report')
        a,b,c=self.mocks()
        with a,b,c as download,self.assertRaisesRegex(ValueError,'reviewed checksum'):self.invoke()
        download.assert_not_called();self.assertEqual(path.read_bytes(),b'earlier report')

    def test_existing_artifact_directory_without_report_is_preserved(self):
        path=self.inputs/'checks/docs';path.mkdir(parents=True);(path/'earlier').write_bytes(b'retain')
        a,b,c=self.mocks()
        with a,b,c as download,self.assertRaisesRegex(ValueError,'existing artifact directory'):self.invoke()
        download.assert_not_called();self.assertEqual((path/'earlier').read_bytes(),b'retain')

    def test_existing_mismatched_document_and_selection_are_preserved(self):
        for kind in ('document','selection'):
            with self.subTest(kind=kind):
                self.inputs=self.root/('inputs-'+kind);path=self.inputs/(self.documents[0]['path'] if kind=='document' else 'release-selection.json')
                path.parent.mkdir(parents=True);path.write_bytes(b'earlier retained bytes')
                a,b,c=self.mocks()
                with a,b,c,self.assertRaisesRegex(ValueError,'Preserving a different existing'):self.invoke()
                self.assertEqual(path.read_bytes(),b'earlier retained bytes')

    def test_wrong_selection_sha_or_source_rejected_before_network(self):
        original=self.selection_sha;self.selection_sha='0'*64
        with patch.object(github,'run') as run,self.assertRaisesRegex(ValueError,'selection checksum'):self.invoke()
        run.assert_not_called();self.selection_sha=original
        self.selection['source_commit']='b'*40;self.save()
        with patch.object(github,'run') as run,self.assertRaisesRegex(ValueError,'selected source'):self.invoke()
        run.assert_not_called()

    def test_all_suite_reports_unique_names_and_nonoverlapping_parents_required(self):
        base=copy.deepcopy(self.selection)
        for change in ('missing','duplicate','same-parent','nested-parent','wrong-basename','traversal'):
            self.selection=copy.deepcopy(base)
            if change=='missing':self.selection['checks'].pop()
            if change=='duplicate':self.selection['checks'][-1]=copy.deepcopy(self.selection['checks'][0])
            if change=='same-parent':self.selection['checks'][1]['path']='checks/docs/python.json'
            if change=='nested-parent':self.selection['checks'][1]['path']='checks/docs/nested/python.json'
            if change=='wrong-basename':self.selection['checks'][0]['path']='checks/docs/report.json'
            if change=='traversal':self.selection['checks'][0]['path']='../docs.json'
            self.save()
            with self.subTest(change=change),patch.object(github,'run') as run,self.assertRaises(ValueError):self.invoke()
            run.assert_not_called()

    def test_source_run_failure_prevents_download_and_input_creation(self):
        with patch.object(github,'run',side_effect=ValueError('not authenticated')),patch.object(github,'gh') as download,self.assertRaisesRegex(ValueError,'authenticated'):self.invoke()
        download.assert_not_called();self.assertFalse(self.inputs.exists())

    def test_artifact_expiry_or_bad_download_bytes_fail_closed(self):
        a,b,c=self.mocks()
        with a,patch.object(github,'artifact',side_effect=ValueError('expired')),c as download,self.assertRaisesRegex(ValueError,'expired'):self.invoke()
        download.assert_not_called()
        self.hosted['docs']=b'counterfeit bytes'
        a,b,c=self.mocks()
        with a,b,c,self.assertRaisesRegex(ValueError,'reviewed checksum'):self.invoke()

    def test_images_require_separate_explicit_retained_export(self):
        self.selection['image']={'directory':'retained/zero'};self.save()
        with patch.object(github,'run') as run,self.assertRaisesRegex(ValueError,'Restore the exact selected image'):self.invoke()
        run.assert_not_called()

    def test_trusted_document_source_and_staging_paths_reject_symlinks(self):
        path=self.repo/self.documents[0]['path'];body=path.read_bytes();path.unlink();other=self.repo/'other';other.write_bytes(body);path.symlink_to(other)
        a,b,c=self.mocks()
        with a,b,c,self.assertRaisesRegex(ValueError,'Redirected local input'):self.invoke()
        self.inputs=self.root/'linked-inputs';self.inputs.symlink_to(self.root/'inputs',target_is_directory=True)
        with patch.object(github,'run') as run,self.assertRaisesRegex(ValueError,'unredirected staging'):self.invoke()
        run.assert_not_called()

    def test_cli_report_cannot_overwrite_staged_inputs(self):
        with self.assertRaises(SystemExit):stage.main(['--repository',REPO,'--run-id','101','--source-sha',SOURCE,'--selection',self.selection_path,'--selection-sha256',self.selection_sha,'--inputs-root',str(self.inputs),'--report',str(self.inputs/'release-selection.json')])
        self.assertFalse(self.inputs.exists())


if __name__=='__main__':unittest.main()
