"""A restricted check must preserve limitations and require separate consent."""
from copy import deepcopy
import unittest

from workbench.review.providers import DemoProvider, examples
from workbench.review.schemas import Request, validate_draft
from workbench.review.tool_worker import calculate, TOOL_VERSION
from workbench.review import render


class FormulaScopeTests(unittest.TestCase):
    def setUp(self):
        self.request = Request.model_validate(examples()[1]['request']).model_dump(mode='json')
        self.draft = DemoProvider().generate('draft', self.request)
        self.reading = self.draft['readings'][0]
        self.reading['qualifications'] = [{'kind': 'meaning', 'description': 'Interpretation remains unresolved.', 'representation': 'unencoded', 'rationale': 'Preserved, not silently deleted.'}]
        self.reading['formal_scope'] = {'mode': 'declared_formulas', 'rationale': 'Only these formulas; the original interpretation remains unresolved.'}
        self.review = {k: 'disputed' for k in ('fidelity', 'attribution', 'interpretation')}
        self.review.update(approve_formal_mapping=True, approve_formula_scope=True)

    def run_check(self, operation='formal', version=TOOL_VERSION):
        return calculate({'operation': operation, 'request': self.request, 'draft': self.draft,
                          'human_review': self.review, 'tool_version': version})

    def test_disputed_interpretation_can_have_explicit_conditional_check(self):
        result = self.run_check()['results'][0]
        self.assertEqual(result['outcome'], 'no_counterexample_complete')
        self.assertEqual(result['excluded_qualifications'][0]['description'], 'Interpretation remains unresolved.')
        self.assertIn('not validity of the whole passage', result['scope'])
        self.assertEqual(self.reading['qualifications'][0]['representation'], 'unencoded')

    def test_scope_requires_both_confirmations(self):
        for key in ('approve_formula_scope', 'approve_formal_mapping'):
            with self.subTest(key=key):
                self.review[key] = False
                self.assertEqual(self.run_check()['results'][0]['outcome'], 'review_required')
                self.review[key] = True

    def test_legacy_gate_unchanged_without_scope(self):
        self.reading['formal_scope'] = None
        self.review.update({k: 'accepted' for k in ('fidelity','attribution','interpretation')})
        self.assertEqual(self.run_check()['results'][0]['outcome'], 'unsupported_semantics')

    def test_old_tool_version_cannot_gain_new_scope(self):
        with self.assertRaisesRegex(ValueError, 'requires review-tools-v3'):
            self.run_check(version='review-tools-v2')

    def test_scope_does_not_excuse_missing_formulas(self):
        self.reading['formal_mapping']['formulas'].pop(self.reading['target'])
        self.assertEqual(self.run_check()['results'][0]['outcome'], 'incomplete_mapping')

    def test_scope_does_not_excuse_cycles_or_blank_description(self):
        self.reading['formal_scope']['rationale'] = ' ' * 25
        with self.assertRaisesRegex(ValueError, 'scope'):
            validate_draft(self.draft, self.request)
        self.reading['formal_scope']['rationale'] = 'Only the declared formulas and dependencies.'
        next(n for n in self.reading['nodes'] if n['id'] == self.reading['target'])['depends_on'] = [self.reading['target']]
        with self.assertRaisesRegex(ValueError, 'Cyclic'):
            validate_draft(self.draft, self.request)

    def test_candidate_never_claims_reader_approval(self):
        result = self.run_check('audit')
        self.assertFalse(result['candidate_checks'][0]['human_approved'])
        self.assertIn('unsupported_semantics', [i['kind'] for i in result['issues']])

    def test_equivocation_and_visible_bridge_change_result(self):
        reading = self.reading
        nodes = reading['nodes']
        first, second = nodes[0]['id'], nodes[1]['id']
        target = reading['target']
        reading['formal_mapping']['formulas'] = {first: 'M', second: 'M -> M', target: 'H'}
        next(n for n in nodes if n['id'] == target)['depends_on'] = [first, second]
        self.assertEqual(self.run_check()['results'][0]['outcome'], 'witness_found')
        reading['formal_mapping']['formulas'][target] = 'M'
        self.assertEqual(self.run_check()['results'][0]['outcome'], 'no_counterexample_complete')
        reading['formal_mapping']['formulas'][target] = 'H'
        reading['formal_mapping']['formulas'][second] = 'M -> H'
        self.assertEqual(self.run_check()['results'][0]['outcome'], 'no_counterexample_complete')


if __name__ == '__main__':
    unittest.main()
