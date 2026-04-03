"""Tests for news domain registry."""

from __future__ import annotations

from code_review_agent.news.domains import (
    DOMAIN_REGISTRY,
    list_domains,
    resolve_domain,
)


class TestDomainRegistry:
    def test_has_hackernews(self) -> None:
        assert "hackernews" in DOMAIN_REGISTRY
        assert DOMAIN_REGISTRY["hackernews"].source == "Hacker News"

    def test_has_multiple_categories(self) -> None:
        categories = {c.category for c in DOMAIN_REGISTRY.values()}
        assert "tech" in categories
        assert "ai" in categories
        assert "security" in categories

    def test_all_have_urls(self) -> None:
        for name, config in DOMAIN_REGISTRY.items():
            assert config.url, f"{name} has no URL"
            assert config.url.startswith("http"), f"{name} URL invalid: {config.url}"

    def test_registry_size(self) -> None:
        assert len(DOMAIN_REGISTRY) >= 30


class TestResolveDomain:
    def test_single_domain(self) -> None:
        configs = resolve_domain("hackernews")
        assert len(configs) == 1
        assert configs[0].name == "hackernews"

    def test_meta_domain(self) -> None:
        configs = resolve_domain("tech")
        assert len(configs) == 3
        names = {c.name for c in configs}
        assert "hackernews" in names

    def test_unknown_domain(self) -> None:
        assert resolve_domain("nonexistent") == []


class TestListDomains:
    def test_includes_categories(self) -> None:
        output = list_domains()
        assert "[TECH]" in output
        assert "[AI]" in output
        assert "hackernews" in output

    def test_includes_meta_domains(self) -> None:
        output = list_domains()
        assert "Meta-domains" in output
        assert "tech" in output
