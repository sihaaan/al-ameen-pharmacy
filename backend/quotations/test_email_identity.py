from unittest import TestCase

from .email_identity import (
    canonical_email_addresses,
    canonical_singleton_from_address,
    canonicalize_email_address,
    canonicalize_email_domain,
    is_private_email_domain,
    is_public_email_domain,
)


class EmailIdentityTests(TestCase):
    def test_domain_canonicalization_handles_case_root_dot_and_idn(self):
        self.assertEqual(canonicalize_email_domain(" Example.COM. "), "example.com")
        self.assertEqual(canonicalize_email_domain("example.com\u3002"), "example.com")
        self.assertEqual(
            canonicalize_email_domain("b\u00fccher.example"),
            "xn--bcher-kva.example",
        )
        self.assertEqual(
            canonicalize_email_domain("xn--bcher-kva.example"),
            "xn--bcher-kva.example",
        )

    def test_idna_processing_is_non_transitional(self):
        self.assertEqual(canonicalize_email_domain("fa\u00df.de"), "xn--fa-hia.de")
        self.assertEqual(canonicalize_email_domain("fass.de"), "fass.de")
        self.assertNotEqual(
            canonicalize_email_domain("fa\u00df.de"),
            canonicalize_email_domain("fass.de"),
        )

    def test_invalid_non_dns_and_ip_domains_fail_closed(self):
        invalid_domains = (
            "",
            "localhost",
            "127.0.0.1",
            "[2001:db8::1]",
            "bad..example",
            "example.com..",
            "-bad.example",
            "bad_.example",
            f"{'a' * 64}.example",
            ".".join(["a" * 63] * 5),
        )
        for domain in invalid_domains:
            with self.subTest(domain=domain):
                self.assertEqual(canonicalize_email_domain(domain), "")
                self.assertFalse(is_public_email_domain(domain))
                self.assertFalse(is_private_email_domain(domain))

    def test_address_matching_lowercases_but_preserves_dots_and_plus_tags(self):
        self.assertEqual(
            canonicalize_email_address(" Buyer.Name+RFQ@B\u00dcCHER.Example. "),
            "buyer.name+rfq@xn--bcher-kva.example",
        )
        self.assertNotEqual(
            canonicalize_email_address("buyer+rfq@example.com"),
            canonicalize_email_address("buyer@example.com"),
        )
        self.assertNotEqual(
            canonicalize_email_address("buyer.name@example.com"),
            canonicalize_email_address("buyername@example.com"),
        )

    def test_header_extraction_canonicalizes_and_deduplicates(self):
        self.assertEqual(
            canonical_email_addresses(
                (
                    "Buyer <Buyer@Example.COM.>",
                    "buyer@example.com, Other <other@example.com>",
                )
            ),
            frozenset({"buyer@example.com", "other@example.com"}),
        )

    def test_singleton_from_requires_one_field_and_one_address_token(self):
        self.assertEqual(
            canonical_singleton_from_address(
                "Collapsed <buyer@example.com>",
                from_header_values=("Buyer <BUYER@Example.COM.>",),
            ),
            "buyer@example.com",
        )

        ambiguous_cases = (
            # Multiple addresses in the only physical From field.
            ("buyer@example.com, other@example.com", None),
            # The same address repeated in one field is still ambiguous.
            (
                "buyer@example.com",
                ("buyer@example.com, buyer@example.com",),
            ),
            # Duplicate physical From fields cannot be hidden by a collapsed
            # sender projection.
            (
                "buyer@example.com",
                ("buyer@example.com", "buyer@example.com"),
            ),
            # One malformed token beside a valid address invalidates the field.
            (
                "buyer@example.com",
                ("not-an-address, Buyer <buyer@example.com>",),
            ),
        )
        for sender, header_values in ambiguous_cases:
            with self.subTest(sender=sender, header_values=header_values):
                self.assertEqual(
                    canonical_singleton_from_address(
                        sender,
                        from_header_values=header_values,
                    ),
                    "",
                )

    def test_malformed_addresses_fail_closed(self):
        invalid_addresses = (
            "@example.com",
            ".buyer@example.com",
            "buyer.@example.com",
            "buyer..rfq@example.com",
            "buyer name@example.com",
            "buyer@localhost",
            "buyer@127.0.0.1",
        )
        for address in invalid_addresses:
            with self.subTest(address=address):
                self.assertEqual(canonicalize_email_address(address), "")

    def test_public_provider_variants_and_subdomains_are_not_private(self):
        public_domains = (
            "gmail.com.",
            "mail.gmail.com",
            "yahoo.fr",
            "mail.yahoo.co.jp",
            "outlook.de",
            "outlook.ae",
            "mail.outlook.com.au",
            "protonmail.ch",
            "pm.me",
            "aim.com",
            "hey.com",
            "mac.com",
            "rocketmail.com",
        )
        for domain in public_domains:
            with self.subTest(domain=domain):
                self.assertTrue(is_public_email_domain(domain))
                self.assertFalse(is_private_email_domain(domain))

    def test_public_provider_lookalike_suffixes_remain_private(self):
        private_domains = (
            "gmail.com.attacker.com",
            "yahoo.fr.attacker.example",
            "outlook.de.customer.example",
            "protonmail.ch.example",
        )
        for domain in private_domains:
            with self.subTest(domain=domain):
                self.assertFalse(is_public_email_domain(domain))
                self.assertTrue(is_private_email_domain(domain))
