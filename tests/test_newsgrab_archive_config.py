def test_cli_uses_existing_google_news_collector_env(monkeypatch):
    from research.prototype.newsgrab_archive.runner import build_parser

    monkeypatch.setenv("GOOGLE_NEWS_COLLECTOR_URL", "http://collector.example")

    args = build_parser().parse_args(["--query", "600519", "--scope", "company"])

    assert args.base_url == "http://collector.example"
