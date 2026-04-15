from zotero_mcp.acquisition.provenance_parser import (
    parse_provenance_from_extra,
    serialize_provenance_to_extra,
)


class TestParseProvenanceFromExtra:
    def test_normal_case(self):
        extra = "[Paper Ingest Provenance]\naccess_source: unpaywall\nresolver_name: UnpaywallClient"
        result = parse_provenance_from_extra(extra)
        assert result is not None
        assert result["access_source"] == "unpaywall"
        assert result["resolver_name"] == "UnpaywallClient"

    def test_missing_provenance_block(self):
        result = parse_provenance_from_extra("Some manual note without any provenance block")
        assert result is None

    def test_empty_extra(self):
        assert parse_provenance_from_extra("") is None

    def test_none_input(self):
        assert parse_provenance_from_extra(None) is None

    def test_mixed_content_before_block(self):
        extra = "manual note\nanother line\n[Paper Ingest Provenance]\naccess_source: direct"
        result = parse_provenance_from_extra(extra)
        assert result is not None
        assert result["access_source"] == "direct"

    def test_mixed_content_stops_at_next_header(self):
        extra = "[Paper Ingest Provenance]\naccess_source: unpaywall\n[Some Other Block]\nother: data"
        result = parse_provenance_from_extra(extra)
        assert result is not None
        assert "access_source" in result
        assert "other" not in result

    def test_url_value_with_colon(self):
        extra = "[Paper Ingest Provenance]\nproxy_provider: https://proxy.example.com:8080/path"
        result = parse_provenance_from_extra(extra)
        assert result is not None
        assert result["proxy_provider"] == "https://proxy.example.com:8080/path"

    def test_boolean_true_coercion(self):
        extra = "[Paper Ingest Provenance]\nhas_fulltext: true"
        result = parse_provenance_from_extra(extra)
        assert result is not None
        assert result["has_fulltext"] is True
        assert isinstance(result["has_fulltext"], bool)

    def test_boolean_false_coercion(self):
        extra = "[Paper Ingest Provenance]\nhas_fulltext: false"
        result = parse_provenance_from_extra(extra)
        assert result is not None
        assert result["has_fulltext"] is False
        assert isinstance(result["has_fulltext"], bool)

    def test_all_10_schema_fields_parsed(self):
        extra = (
            "[Paper Ingest Provenance]\n"
            "access_source: unpaywall\n"
            "resolver_name: UnpaywallClient\n"
            "proxy_provider: none\n"
            "artifact_type: paper\n"
            "extraction_backend: pdfminer\n"
            "fallback_reason: none\n"
            "license: cc-by\n"
            "has_fulltext: true\n"
            "quality_signal: good\n"
            "bridge_session: none"
        )
        result = parse_provenance_from_extra(extra)
        assert result is not None
        fields = [
            "access_source",
            "resolver_name",
            "proxy_provider",
            "artifact_type",
            "extraction_backend",
            "fallback_reason",
            "license",
            "has_fulltext",
            "quality_signal",
            "bridge_session",
        ]
        for field in fields:
            assert field in result, f"Field '{field}' missing from parsed result"


class TestSerializeProvenanceToExtra:
    def test_basic_serialization(self):
        provenance = {"access_source": "s2", "resolver_name": "SemanticScholarResolver"}
        result = serialize_provenance_to_extra(provenance)
        assert "[Paper Ingest Provenance]" in result
        assert "access_source: s2" in result
        assert "resolver_name: SemanticScholarResolver" in result

    def test_boolean_true_serialized_as_lowercase(self):
        result = serialize_provenance_to_extra({"has_fulltext": True})
        assert "has_fulltext: true" in result

    def test_boolean_false_serialized_as_lowercase(self):
        result = serialize_provenance_to_extra({"has_fulltext": False})
        assert "has_fulltext: false" in result

    def test_url_value_preserved(self):
        result = serialize_provenance_to_extra({"proxy_provider": "https://proxy.example.com:8080"})
        assert "proxy_provider: https://proxy.example.com:8080" in result


class TestRoundTrip:
    def test_full_round_trip(self):
        original = {
            "access_source": "unpaywall",
            "resolver_name": "UnpaywallClient",
            "has_fulltext": True,
            "proxy_provider": "https://proxy.example.com:8080",
            "quality_signal": "good",
        }
        serialized = serialize_provenance_to_extra(original)
        parsed = parse_provenance_from_extra(serialized)
        assert parsed is not None
        assert parsed["access_source"] == original["access_source"]
        assert parsed["resolver_name"] == original["resolver_name"]
        assert parsed["has_fulltext"] is True
        assert parsed["proxy_provider"] == original["proxy_provider"]
        assert parsed["quality_signal"] == original["quality_signal"]
