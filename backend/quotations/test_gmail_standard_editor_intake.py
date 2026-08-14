from django.test import SimpleTestCase, override_settings

from .workflow_features import (
    gmail_standard_editor_intake_enabled,
    quotation_workflow_features,
)


class GmailStandardEditorIntakeFeatureTests(SimpleTestCase):
    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
        QUOTATION_GMAIL_STANDARD_EDITOR_INTAKE_ENABLED=True,
    )
    def test_feature_is_projected_when_both_safety_foundations_are_enabled(self):
        self.assertIs(
            quotation_workflow_features()["gmail_standard_editor_intake"],
            True,
        )
        self.assertIs(gmail_standard_editor_intake_enabled(), True)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
        QUOTATION_GMAIL_STANDARD_EDITOR_INTAKE_ENABLED=True,
    )
    def test_feature_fails_closed_without_review_ui_v2(self):
        self.assertIs(
            quotation_workflow_features()["gmail_standard_editor_intake"],
            False,
        )

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=False,
        QUOTATION_GMAIL_STANDARD_EDITOR_INTAKE_ENABLED=True,
    )
    def test_feature_fails_closed_without_stale_bound_chained_actions(self):
        features = quotation_workflow_features()

        self.assertIs(features["gmail_chained_actions"], False)
        self.assertIs(features["gmail_standard_editor_intake"], False)
        self.assertIs(gmail_standard_editor_intake_enabled(), False)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
        QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED=True,
        QUOTATION_GMAIL_STANDARD_EDITOR_INTAKE_ENABLED=True,
    )
    def test_projection_keeps_unified_route_available_for_flag_rollback(self):
        features = quotation_workflow_features()

        self.assertIs(features["gmail_standard_editor_intake"], True)
        self.assertIs(features["gmail_unified_workspace"], True)

    @override_settings(
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_projection_requires_a_strict_boolean_true(self):
        for configured_value, expected in (
            (True, True),
            (False, False),
            (1, False),
            ("1", False),
            ("true", False),
        ):
            with self.subTest(configured_value=configured_value), override_settings(
                QUOTATION_GMAIL_STANDARD_EDITOR_INTAKE_ENABLED=configured_value
            ):
                self.assertIs(
                    quotation_workflow_features()[
                        "gmail_standard_editor_intake"
                    ],
                    expected,
                )
