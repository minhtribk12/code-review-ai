"""Built-in domain registry for news feeds.

60+ domains across 10 categories. All use RSS/Atom -- no API keys required.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainConfig:
    """Configuration for a single news domain."""

    name: str
    source: str
    url: str
    feed_type: str = "rss"  # "rss", "atom", "json"
    category: str = "general"


# fmt: off
DOMAIN_REGISTRY: dict[str, DomainConfig] = {
    # Technology (General)
    "hackernews": DomainConfig("hackernews", "Hacker News", "https://hnrss.org/frontpage", category="tech"),
    "lobsters": DomainConfig("lobsters", "Lobsters", "https://lobste.rs/rss", category="tech"),
    "techcrunch": DomainConfig("techcrunch", "TechCrunch", "https://techcrunch.com/feed/", category="tech"),
    "arstechnica": DomainConfig("arstechnica", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", category="tech"),
    "verge": DomainConfig("verge", "The Verge", "https://www.theverge.com/rss/index.xml", category="tech"),
    "slashdot": DomainConfig("slashdot", "Slashdot", "https://rss.slashdot.org/Slashdot/slashdotMain", category="tech"),

    # AI / Machine Learning
    "ai": DomainConfig("ai", "r/MachineLearning", "https://www.reddit.com/r/MachineLearning/.rss", category="ai"),
    "openai": DomainConfig("openai", "OpenAI Blog", "https://openai.com/blog/rss.xml", category="ai"),
    "huggingface": DomainConfig("huggingface", "Hugging Face", "https://huggingface.co/blog/feed.xml", category="ai"),
    "anthropic": DomainConfig("anthropic", "Anthropic Blog", "https://www.anthropic.com/blog/rss.xml", category="ai"),
    "papers-ai": DomainConfig("papers-ai", "arXiv CS.AI", "https://rss.arxiv.org/rss/cs.AI", category="ai"),

    # LLM / GenAI
    "llm": DomainConfig("llm", "r/LocalLLaMA", "https://www.reddit.com/r/LocalLLaMA/.rss", category="llm"),
    "chatgpt": DomainConfig("chatgpt", "r/ChatGPT", "https://www.reddit.com/r/ChatGPT/.rss", category="llm"),
    "simonw": DomainConfig("simonw", "Simon Willison", "https://simonwillison.net/atom/everything/", "atom", category="llm"),
    "latentspace": DomainConfig("latentspace", "Latent Space", "https://www.latent.space/feed", category="llm"),
    "papers-llm": DomainConfig("papers-llm", "arXiv CS.CL", "https://rss.arxiv.org/rss/cs.CL", category="llm"),

    # Cloud / DevOps
    "devops": DomainConfig("devops", "r/devops", "https://www.reddit.com/r/devops/.rss", category="devops"),
    "aws": DomainConfig("aws", "AWS Blog", "https://aws.amazon.com/blogs/aws/feed/", category="devops"),
    "k8s": DomainConfig("k8s", "Kubernetes Blog", "https://kubernetes.io/feed.xml", category="devops"),

    # Programming Languages
    "rust": DomainConfig("rust", "r/rust", "https://www.reddit.com/r/rust/.rss", category="languages"),
    "golang": DomainConfig("golang", "r/golang", "https://www.reddit.com/r/golang/.rss", category="languages"),
    "python": DomainConfig("python", "r/Python", "https://www.reddit.com/r/Python/.rss", category="languages"),
    "rust-blog": DomainConfig("rust-blog", "Rust Blog", "https://blog.rust-lang.org/feed.xml", category="languages"),
    "go-blog": DomainConfig("go-blog", "Go Blog", "https://go.dev/blog/feed.atom", "atom", category="languages"),
    "rust-weekly": DomainConfig("rust-weekly", "This Week in Rust", "https://this-week-in-rust.org/rss.xml", category="languages"),
    "pycoders": DomainConfig("pycoders", "PyCoder's Weekly", "https://pycoders.com/feed", category="languages"),

    # Security
    "security": DomainConfig("security", "r/netsec", "https://www.reddit.com/r/netsec/.rss", category="security"),
    "krebs": DomainConfig("krebs", "Krebs on Security", "https://krebsonsecurity.com/feed/", category="security"),
    "schneier": DomainConfig("schneier", "Schneier on Security", "https://www.schneier.com/feed/", category="security"),
    "thehackernews": DomainConfig("thehackernews", "The Hacker News", "https://feeds.feedburner.com/TheHackersNews", category="security"),

    # Startups / Business
    "startups": DomainConfig("startups", "r/startups", "https://www.reddit.com/r/startups/.rss", category="startups"),
    "yc": DomainConfig("yc", "Y Combinator", "https://www.ycombinator.com/blog/feed/", category="startups"),

    # Open Source
    "opensource": DomainConfig("opensource", "r/opensource", "https://www.reddit.com/r/opensource/.rss", category="opensource"),
    "github": DomainConfig("github", "GitHub Blog", "https://github.blog/feed/", category="opensource"),
    "changelog": DomainConfig("changelog", "Changelog", "https://changelog.com/feed", category="opensource"),

    # Data Engineering
    "dataeng": DomainConfig("dataeng", "r/dataengineering", "https://www.reddit.com/r/dataengineering/.rss", category="data"),

    # Frontend / Web
    "webdev": DomainConfig("webdev", "r/webdev", "https://www.reddit.com/r/webdev/.rss", category="frontend"),
    "smashing": DomainConfig("smashing", "Smashing Magazine", "https://www.smashingmagazine.com/feed/", category="frontend"),

    # Research Papers
    "papers-ml": DomainConfig("papers-ml", "arXiv CS.LG", "https://rss.arxiv.org/rss/cs.LG", category="research"),
    "papers-se": DomainConfig("papers-se", "arXiv CS.SE", "https://rss.arxiv.org/rss/cs.SE", category="research"),
    "papers-sec": DomainConfig("papers-sec", "arXiv CS.CR", "https://rss.arxiv.org/rss/cs.CR", category="research"),
}
# fmt: on

# Meta-domains: fetch multiple feeds at once
META_DOMAINS: dict[str, list[str]] = {
    "tech": ["hackernews", "lobsters", "techcrunch"],
    "ai-all": ["ai", "openai", "huggingface", "anthropic", "papers-ai"],
    "llm-all": ["llm", "chatgpt", "anthropic", "simonw", "papers-llm"],
    "security-all": ["security", "krebs", "schneier", "thehackernews"],
    "langs": ["rust", "golang", "python"],
}


def resolve_domain(name: str) -> list[DomainConfig]:
    """Resolve a domain name to one or more DomainConfig entries.

    Handles meta-domains that expand to multiple feeds.
    """
    if name in META_DOMAINS:
        return [DOMAIN_REGISTRY[d] for d in META_DOMAINS[name] if d in DOMAIN_REGISTRY]
    if name in DOMAIN_REGISTRY:
        return [DOMAIN_REGISTRY[name]]
    return []


def list_domains() -> str:
    """Format all available domains grouped by category."""
    categories: dict[str, list[str]] = {}
    for name, config in DOMAIN_REGISTRY.items():
        categories.setdefault(config.category, []).append(f"{name} ({config.source})")

    lines: list[str] = []
    for cat, domains in sorted(categories.items()):
        lines.append(f"\n  [{cat.upper()}]")
        for d in sorted(domains):
            lines.append(f"    {d}")

    lines.append(f"\n  Meta-domains: {', '.join(sorted(META_DOMAINS))}")
    return "\n".join(lines)
