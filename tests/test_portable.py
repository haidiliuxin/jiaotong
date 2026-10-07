"""Tiny byte fixtures test bundle integrity, not native simulation performance."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from traffic_agent.portable import KIND, MANIFEST, PLANNING_PROFILE, PLANNING_FILES, digest, verify_bundle


class PortableTests(unittest.TestCase):
    def fixture(self,root):
        names=('launch.cmd','python/python.exe','python/python312.dll','python/python312._pth','python/python312.zip','python/pyvenv.cfg',
               'traffic_agent/__main__.py','traffic_agent/portable.py','start.ps1','案例/道路.txt',
               'traffic_agent/portable_compiler.py','portable-tools/gcc.exe','toolchain/bin/gcc.exe')
        files={}
        for name in names:
            path=root/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'fixture')
            files[name]=dict(bytes=path.stat().st_size,sha256=digest(path))
        self.write(root,dict(kind=KIND,code_commit='test',python_version='3.12.fixture',files=files))

    def write(self,root,manifest):
        (root/MANIFEST).write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')

    def test_relocation_and_generated_outputs_do_not_change_sealed_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'source'; self.fixture(root)
            target=Path(temp)/'移动 目录'; shutil.copytree(root,target)
            output=target/'智能体实验/jobs/new/state.json'; output.parent.mkdir(parents=True)
            output.write_text('{}',encoding='utf-8')
            self.assertEqual(verify_bundle(target)['files'],13)
            self.assertEqual(verify_bundle(target)['new_model_calls'],0)

    def test_missing_extra_or_same_size_change_fails(self):
        for defect in ('missing','extra','same_size'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as temp:
                root=Path(temp); self.fixture(root)
                if defect=='missing':
                    (root/'launch.cmd').unlink()
                elif defect=='extra':
                    (root/'unregistered.py').write_text('pass',encoding='utf-8')
                else:
                    (root/'launch.cmd').write_bytes(b'FIXTURE')
                with self.assertRaises(ValueError): verify_bundle(root)

    def test_unsafe_or_case_ambiguous_manifest_paths_fail(self):
        for name in ('../escape','C:/escape','python/PYTHON.exe','CON.txt','name.','a\\b'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                root=Path(temp); self.fixture(root)
                manifest=json.loads((root/MANIFEST).read_text(encoding='utf-8'))
                manifest['files'][name]=dict(bytes=0,sha256='')
                self.write(root,manifest)
                with self.assertRaises(ValueError): verify_bundle(root)

    def test_lfs_pointer_never_counts_as_delivered_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root)
            path=root/'python/python.exe'
            path.write_bytes(b'version https://git-lfs.github.com/spec/v1\noid sha256:0\n')
            manifest=json.loads((root/MANIFEST).read_text(encoding='utf-8'))
            manifest['files']['python/python.exe']=dict(bytes=path.stat().st_size,sha256=digest(path))
            self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'LFS'): verify_bundle(root)

    def test_missing_bootstrap_cannot_be_hidden_by_matching_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text(encoding='utf-8'))
            del manifest['files']['launch.cmd']; self.write(root,manifest)
            (root/'launch.cmd').unlink()
            with self.assertRaisesRegex(ValueError,'缺少启动器'): verify_bundle(root)

    def test_planning_release_requires_its_whole_programme_verifier_and_ui(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['application_profile']=PLANNING_PROFILE
            self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
            for name in PLANNING_FILES:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b'fixture')
                manifest['files'][name]=dict(bytes=7,sha256=digest(path))
            self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])
            for name in ('scripts/verify_programme.py','traffic_agent/static/planner_view.js',
                         'traffic_agent/planning_response.py','traffic_agent/study_handoff.py',
                         'traffic_agent/professional_review.py','traffic_agent/knowledge_retrieval.py',
                         'traffic_agent/knowledge.json'):
                with self.subTest(missing=name):
                    expected=manifest['files'].pop(name)
                    path=root/name;content=path.read_bytes();path.unlink();self.write(root,manifest)
                    with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                    path.write_bytes(content);manifest['files'][name]=expected;self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])

    def test_older_planning_profile_keeps_its_original_file_requirements(self):
        from traffic_agent.portable import LEGACY_PLANNING_PROFILE,LEGACY_PLANNING_FILES
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['application_profile']=LEGACY_PLANNING_PROFILE
            for name in LEGACY_PLANNING_FILES:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
                manifest['files'][name]=dict(bytes=7,sha256=digest(path))
            self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])
            manifest['application_profile']=PLANNING_PROFILE;self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)

    def test_new_conclusion_protocol_requires_its_action_facts_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['planning_capabilities']=dict(conclusion_protocol='measured-conclusion-v2')
            self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
            name='traffic_agent/executed_facts.py';path=root/name;path.write_bytes(b'fixture')
            manifest['files'][name]=dict(bytes=7,sha256=digest(path));self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])

    def test_packager_rejects_protected_settings_and_literal_keys_without_echo(self):
        from scripts.build_portable import check_application_credentials
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir()
            path=root/'config'/'defaults.json';path.write_text('{"model":"deepseek-v4-flash"}')
            self.assertTrue(check_application_credentials(root)['passed'])
            path.write_bytes(('sk_'+'x'*32).encode())
            with self.assertRaisesRegex(ValueError,'停止打包') as error:check_application_credentials(root)
            self.assertNotIn('x'*32,str(error.exception))
            path.write_text('{}');(root/'config'/'llm.json').write_text('{"secret":"protected"}')
            with self.assertRaisesRegex(ValueError,'停止打包'):check_application_credentials(root)

    def test_measurement_and_selection_require_modules_only_when_declared(self):
        declarations=[('hypothesis_contract_protocol','hypothesis-measurement-v1',['traffic_agent/hypothesis_contract.py']),
                      ('planning_input_protocol','referenced-planning-input-v1',['traffic_agent/planning_payload.py']),
                      ('planning_input_protocol','referenced-planning-input-v2',['traffic_agent/planning_payload.py']),
                      ('risk_review_protocol','observed-risk-boundary-v1',['traffic_agent/risk_review.py']),
                      ('risk_review_protocol','observed-risk-boundary-v2',['traffic_agent/risk_review.py']),
                      ('choice_rationale_protocol','choice-rationale-v1',['traffic_agent/choice_rationale.py','traffic_agent/choice_evidence.py']),
                      ('initial_hypothesis_correction_protocol','initial-hypothesis-correction-v1',['traffic_agent/initial_hypothesis_review.py']),
                      ('initial_hypothesis_correction_protocol','initial-hypothesis-correction-v2',['traffic_agent/initial_hypothesis_review.py','traffic_agent/hypothesis_policy_patch.py']),
                      ('initial_hypothesis_correction_protocol','initial-hypothesis-correction-v3',['traffic_agent/initial_hypothesis_review.py','traffic_agent/hypothesis_policy_patch.py','traffic_agent/hypothesis_field_patch.py']),
                      ('hypothesis_field_patch_protocol','hypothesis-field-patch-v1',['traffic_agent/hypothesis_field_patch.py']),
                      ('hypothesis_policy_patch_protocol','hypothesis-policy-patch-v1',['traffic_agent/hypothesis_policy_patch.py']),
                      ('choice_basis_protocol','observed-choice-basis-v1',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js']),
                      ('choice_basis_protocol','observed-choice-basis-v2',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js']),
                      ('choice_basis_protocol','observed-choice-basis-v3',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js']),
                      ('choice_basis_protocol','observed-choice-basis-v4',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js']),
                      ('choice_basis_protocol','observed-choice-basis-v5',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js']),
                      ('choice_basis_protocol','observed-choice-basis-v6',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js','traffic_agent/candidate_comparison.py']),
                      ('choice_basis_protocol','observed-choice-basis-v7',['traffic_agent/choice_evidence.py','traffic_agent/static/choice_view.js','traffic_agent/candidate_comparison.py']),
                      ('question_wording_protocol','bounded-question-wording-v1',['traffic_agent/question_wording.py']),
                      ('question_wording_protocol','bounded-question-wording-v2',['traffic_agent/question_wording.py']),
                      ('question_wording_protocol','bounded-question-wording-v3',['traffic_agent/question_wording.py']),
                      ('question_wording_protocol','bounded-question-wording-v4',['traffic_agent/question_wording.py']),
                      ('question_wording_protocol','bounded-question-wording-v5',['traffic_agent/question_wording.py']),
                      ('question_wording_protocol','bounded-question-wording-v6',['traffic_agent/question_wording.py','traffic_agent/hypothesis_contract.py']),
                      ('question_authoring_protocol','named-question-authoring-v1',['traffic_agent/question_authoring.py','traffic_agent/hypothesis_contract.py']),
                      ('direction_semantics_protocol','oneway-direction-v4',['traffic_agent/direction_claims.py']),
                      ('action_execution_protocol','action-execution-v1',['traffic_agent/action_execution.py']),
                      ('action_execution_protocol','action-execution-v2',['traffic_agent/action_execution.py']),
                      ('planning_version','natural-strategy-plan-v10',
                       ['traffic_agent/study_objectives.py','traffic_agent/study_depth.py','traffic_agent/hypothesis_contract.py','traffic_agent/question_authoring.py']),
                      ('planning_version','natural-strategy-plan-v8',
                       ['traffic_agent/study_objectives.py','traffic_agent/study_depth.py','traffic_agent/hypothesis_contract.py']),
                      ('planning_version','natural-strategy-plan-v9',
                       ['traffic_agent/study_objectives.py','traffic_agent/study_depth.py','traffic_agent/hypothesis_contract.py'])]
        for field,version,names in declarations:
            with self.subTest(protocol=version),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                manifest=json.loads((root/MANIFEST).read_text('utf-8'))
                self.assertTrue(verify_bundle(root)['passed'])
                manifest['planning_capabilities']={field:version};self.write(root,manifest)
                for name in names:
                    with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
                    manifest['files'][name]=dict(bytes=7,sha256=digest(path));self.write(root,manifest)
                self.assertTrue(verify_bundle(root)['passed'])

    def test_v3_conclusion_bundle_requires_numeric_scope_and_policy_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['planning_capabilities']=dict(conclusion_protocol='measured-conclusion-v3')
            for name in ('traffic_agent/executed_facts.py','traffic_agent/measured_numbers.py'):
                self.write(root,manifest)
                with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                path=root/name;path.write_bytes(b'fixture')
                manifest['files'][name]=dict(bytes=7,sha256=digest(path))
            self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])

    def test_v6_v7_conclusion_bundle_requires_typed_public_claims(self):
        for version in ('measured-conclusion-v6','measured-conclusion-v7','measured-conclusion-v8','measured-conclusion-v9','measured-conclusion-v10','measured-conclusion-v11','measured-conclusion-v12'):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                manifest=json.loads((root/MANIFEST).read_text('utf-8'))
                manifest['planning_capabilities']=dict(conclusion_protocol=version)
                for name in ('traffic_agent/executed_facts.py','traffic_agent/measured_numbers.py',
                             'traffic_agent/public_claims.py'):
                    self.write(root,manifest)
                    with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                    path=root/name;path.write_bytes(b'fixture')
                    manifest['files'][name]=dict(bytes=7,sha256=digest(path))
                self.write(root,manifest)
                self.assertTrue(verify_bundle(root)['passed'])

    def test_rag_v2_bundle_requires_feasibility_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['planning_capabilities']=dict(knowledge_retrieval_protocol='curated-knowledge-rag-v1')
            self.write(root,manifest);self.assertTrue(verify_bundle(root)['passed'])
            manifest['planning_capabilities']['knowledge_retrieval_protocol']='curated-knowledge-rag-v2'
            self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
            name='traffic_agent/feasibility_search.py';(root/name).write_bytes(b'fixture')
            manifest['files'][name]=dict(bytes=7,sha256=digest(root/name))
            self.write(root,manifest);self.assertTrue(verify_bundle(root)['passed'])

    def test_v13_and_v14_bundles_require_typed_metric_assertions(self):
        for version in ('measured-conclusion-v13','measured-conclusion-v14','measured-conclusion-v15'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                manifest=json.loads((root/MANIFEST).read_text('utf-8'))
                manifest['planning_capabilities']=dict(conclusion_protocol=version)
                for name in ('traffic_agent/executed_facts.py','traffic_agent/measured_numbers.py',
                             'traffic_agent/public_claims.py','traffic_agent/metric_assertions.py','traffic_agent/hypothesis_contract.py'):
                    self.write(root,manifest)
                    with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                    path=root/name;path.write_bytes(b'fixture')
                    manifest['files'][name]=dict(bytes=7,sha256=digest(path))
                self.write(root,manifest)
                self.assertTrue(verify_bundle(root)['passed'])

    def test_v16_bundle_requires_evidence_coverage_validator(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['planning_capabilities']=dict(conclusion_protocol='measured-conclusion-v16')
            for name in ('traffic_agent/executed_facts.py','traffic_agent/measured_numbers.py',
                         'traffic_agent/public_claims.py','traffic_agent/metric_assertions.py','traffic_agent/hypothesis_contract.py'):
                (root/name).write_bytes(b'fixture');manifest['files'][name]=dict(bytes=7,sha256=digest(root/name))
            self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
            name='traffic_agent/evidence_support.py';(root/name).write_bytes(b'fixture')
            manifest['files'][name]=dict(bytes=7,sha256=digest(root/name))
            self.write(root,manifest);self.assertTrue(verify_bundle(root)['passed'])

    def test_scoped_review_bundle_requires_both_new_modules(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['planning_capabilities']=dict(hypothesis_review_scope_protocol='hypothesis-review-scope-v1')
            for name in ('traffic_agent/review_scope.py','traffic_agent/study_comparison.py'):
                self.write(root,manifest)
                with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                path=root/name;path.write_bytes(b'fixture')
                manifest['files'][name]=dict(bytes=7,sha256=digest(path))
            self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])

    def test_stage_instruction_bundle_requires_its_prompt_catalog(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            manifest=json.loads((root/MANIFEST).read_text('utf-8'))
            manifest['planning_capabilities']=dict(feedback_instruction_protocol='stage-planning-instructions-v1')
            self.write(root,manifest)
            with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
            path=root/'traffic_agent/planning_instructions.py';path.write_bytes(b'fixture')
            manifest['files']['traffic_agent/planning_instructions.py']=dict(bytes=7,sha256=digest(path))
            self.write(root,manifest)
            self.assertTrue(verify_bundle(root)['passed'])

    def test_scoped_reference_and_comparison_bundles_require_their_measured_contracts(self):
        for field,version,module in [('finding_reference_schema','scoped-measured-findings-v2','research_evidence'),
                                     ('study_comparison_protocol','study-comparison-v2','study_comparison')]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);self.fixture(root)
                manifest=json.loads((root/MANIFEST).read_text('utf-8'))
                manifest['planning_capabilities']={field:version}
                for name in (f'traffic_agent/{module}.py','traffic_agent/hypothesis_contract.py'):
                    self.write(root,manifest)
                    with self.assertRaisesRegex(ValueError,'整个任务核验器'):verify_bundle(root)
                    path=root/name;path.write_bytes(b'fixture')
                    manifest['files'][name]=dict(bytes=7,sha256=digest(path))
                self.write(root,manifest)
                self.assertTrue(verify_bundle(root)['passed'])

    def test_loaded_planning_protocols_must_match_new_bundle_manifest(self):
        from scripts.validate_portable_environment import check_planning_runtime
        from traffic_agent.natural_plan import capabilities
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'bundle';poison=Path(temp)/'empty_user'
            planning=dict(config=dict(key_configured=False),settings_path=str(poison/'ITS-TrafficAgent/llm.json'),
                          modules=dict(planner=str(root/'traffic_agent/natural_plan.py')),capabilities=capabilities())
            manifest=dict(planning_capabilities=capabilities())
            self.assertTrue(check_planning_runtime(manifest,planning,root,poison)['verified'])
            for key in ('initial_review_protocol','initial_budget_protocol','study_handoff_protocol','routing_budget_protocol'):
                wrong=dict(planning,capabilities=dict(planning['capabilities'],**{key:'old-protocol'}))
                with self.subTest(key=key),self.assertRaisesRegex(ValueError,'实际加载的规划协议'):
                    check_planning_runtime(manifest,wrong,root,poison)
            self.assertFalse(check_planning_runtime({},dict(planning,capabilities=None),root,poison)['verified'])
            for wrong in (dict(planning,config=dict(key_configured=True)),
                          dict(planning,modules=dict(planner=str(Path(temp)/'host/natural_plan.py'))),
                          dict(planning,settings_path=str(Path(temp)/'host/llm.json'))):
                with self.assertRaisesRegex(ValueError,'隔离失败'):
                    check_planning_runtime(manifest,wrong,root,poison)


if __name__ == '__main__':
    unittest.main()
