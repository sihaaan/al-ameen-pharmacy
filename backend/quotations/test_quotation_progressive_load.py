from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from .models import Company, Quotation
from .serializers import QuotationSerializer
from .workflow_features import quotation_workflow_features


class QuotationEditorProgressiveLoadFeatureTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="progressive-load-staff",
            is_staff=True,
        )
        self.company = Company.objects.create(
            name="Progressive Load Customer",
        )
        self.quotation = Quotation.objects.create(
            company=self.company,
            created_by=self.staff,
        )

    @override_settings(QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=False)
    def test_feature_is_disabled_by_default_configuration(self):
        self.assertIs(
            quotation_workflow_features()["quotation_editor_progressive_load"],
            False,
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
                QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=configured_value
            ):
                self.assertIs(
                    quotation_workflow_features()[
                        "quotation_editor_progressive_load"
                    ],
                    expected,
                )

    @override_settings(
        QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=True,
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_feature_is_independent_of_gmail_flags(self):
        features = quotation_workflow_features()

        self.assertIs(features["quotation_editor_progressive_load"], True)
        self.assertIs(features["gmail_review_ui_v2"], False)
        self.assertIs(features["gmail_chained_actions"], False)

    @override_settings(
        QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=False,
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=True,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
    )
    def test_gmail_features_do_not_enable_progressive_loading(self):
        features = quotation_workflow_features()

        self.assertIs(features["quotation_editor_progressive_load"], False)
        self.assertIs(features["gmail_review_ui_v2"], True)
        self.assertIs(features["gmail_chained_actions"], True)

    @override_settings(
        QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=True,
        QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
        QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=False,
    )
    def test_quotation_serializer_projects_the_feature(self):
        payload = QuotationSerializer(self.quotation).data

        self.assertIs(
            payload["workflow_features"]["quotation_editor_progressive_load"],
            True,
        )
