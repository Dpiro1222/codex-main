import os
import re
import sys
import json
from datetime import datetime, timezone

import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_json(text: str) -> dict:
    """Extract and parse the first JSON object found in *text*."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"JSON not found in response:\n{text[:300]}")
    return json.loads(match.group())


def print_section(icon: str, title: str) -> None:
    print(f"\n{icon} {title}")
    print("─" * 50)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_env() -> None:
    required = ["ANTHROPIC_API_KEY", "PERPLEXITY_API_KEY", "NOTION_API_KEY", "NOTION_DATABASE_ID"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"❌ 環境変数が未設定です: {', '.join(missing)}")
        print("   .env.example を参考に .env を作成してください。")
        sys.exit(1)


# ---------------------------------------------------------------------------
# STEP 1: Perplexity — information gathering
# ---------------------------------------------------------------------------

def gather_news() -> list[dict]:
    print_section("📡", "STEP 1: Perplexityで最新情報を収集中...")

    prompt = (
        "以下のテーマで過去24時間の最新情報を5件収集。\n"
        "テーマ：AIエージェント/AI自動化、LINEマーケティング、中小企業DX、経営者の課題\n"
        "各件：タイトル・URL・3行要約・Xで使えるポイントをJSON形式で返すこと\n\n"
        "以下のJSON形式のみで返すこと（他のテキスト一切不要）:\n"
        '{"items": [{"title": "...", "url": "https://...", "summary": "...", "x_points": "..."}]}'
    )

    headers = {
        "Authorization": f"Bearer {PERPLEXITY_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "sonar-pro",
        "messages": [{"role": "user", "content": prompt}],
    }

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = parse_json(content)
        items = data.get("items", [])
    except requests.RequestException as exc:
        print(f"❌ Perplexity APIエラー: {exc}")
        sys.exit(1)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"❌ レスポンス解析エラー: {exc}")
        sys.exit(1)

    print(f"✅ {len(items)} 件の情報を収集しました")
    for idx, item in enumerate(items, 1):
        print(f"   {idx}. {item.get('title', '(タイトルなし)')[:60]}")

    return items


# ---------------------------------------------------------------------------
# STEP 2: Claude — scoring
# ---------------------------------------------------------------------------

def score_items(items: list[dict]) -> list[dict]:
    print_section("🎯", "STEP 2: Claudeでスコアリング中...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    passing = []

    for idx, item in enumerate(items, 1):
        title_short = item.get("title", "")[:45]
        print(f"   [{idx}/{len(items)}] {title_short}...")

        prompt = (
            "以下のネタを10点満点でスコアリングしてください。\n\n"
            f"タイトル: {item.get('title', '')}\n"
            f"URL: {item.get('url', '')}\n"
            f"要約: {item.get('summary', '')}\n"
            f"Xポイント: {item.get('x_points', '')}\n\n"
            "採点基準（各1〜10点、平均を出すこと）:\n"
            "1. バズ可能性（驚き・共感・怒り度）\n"
            "2. 経営者・起業家への刺さり度\n"
            "3. AIエージェント会社CEO × LINEマーケ専門家との親和性\n\n"
            "以下のJSON形式のみで返すこと:\n"
            '{"score": <平均点(小数点1位)>, "reason": "採点理由を1〜2文で"}'
        )

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            data = parse_json(resp.content[0].text)
            score = float(data.get("score", 0))
            reason = data.get("reason", "")
        except Exception as exc:
            print(f"      ⚠️  スコアリングスキップ: {exc}")
            continue

        item["score"] = score
        item["score_reason"] = reason
        badge = "✅ 通過" if score >= 7.0 else "❌ スキップ"
        print(f"      スコア: {score}/10  {badge}  — {reason[:60]}")

        if score >= 7.0:
            passing.append(item)

    print(f"\n   📊 {len(items)} 件中 {len(passing)} 件が7点以上で通過")
    return passing


# ---------------------------------------------------------------------------
# STEP 3: Claude — post generation
# ---------------------------------------------------------------------------

def generate_posts(items: list[dict]) -> list[dict]:
    print_section("✍️ ", "STEP 3: Claudeで投稿文を生成中...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    results = []

    for idx, item in enumerate(items, 1):
        title_short = item.get("title", "")[:45]
        print(f"   [{idx}/{len(items)}] {title_short}...")

        prompt = (
            "以下のネタについてX（旧Twitter）の投稿文を3パターン生成してください。\n\n"
            f"タイトル: {item.get('title', '')}\n"
            f"要約: {item.get('summary', '')}\n"
            f"Xポイント: {item.get('x_points', '')}\n\n"
            "発信者プロフィール: AIエージェント会社CEO、LINEマーケ専門、「AI経営便り」運営\n\n"
            "制約（全パターン共通）:\n"
            "- 140文字以内（厳守）\n"
            "- 1行目は数字か問いかけで始める\n"
            "- 最後は問いかけかCTA（行動喚起）で締める\n"
            "- 経営者・起業家向けの言葉遣い\n"
            "- ハッシュタグは最大2個\n\n"
            "パターン定義:\n"
            "A（ノウハウ暴露型）: 具体的なtipsや方法論を明かす\n"
            "B（業界批評型）: 業界の問題点や非常識を指摘する\n"
            "C（数字報告型）: 数字や実績を使って説得力を出す\n\n"
            "以下のJSON形式のみで返すこと:\n"
            '{"pattern_a": "...", "pattern_b": "...", "pattern_c": "..."}'
        )

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            data = parse_json(resp.content[0].text)
            item["pattern_a"] = data.get("pattern_a", "")
            item["pattern_b"] = data.get("pattern_b", "")
            item["pattern_c"] = data.get("pattern_c", "")
        except Exception as exc:
            print(f"      ⚠️  生成スキップ: {exc}")
            continue

        for label, key in [("A", "pattern_a"), ("B", "pattern_b"), ("C", "pattern_c")]:
            count = len(item[key])
            flag = "✅" if count <= 140 else "⚠️ 超過"
            print(f"      パターン{label}: {count}文字 {flag}")

        results.append(item)

    return results


# ---------------------------------------------------------------------------
# STEP 4: Notion — save
# ---------------------------------------------------------------------------

def save_to_notion(items: list[dict]) -> int:
    print_section("📝", "STEP 4: Notionに保存中...")

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    saved = 0

    for idx, item in enumerate(items, 1):
        title_short = item.get("title", "")[:45]
        print(f"   [{idx}/{len(items)}] {title_short}...")

        url_value = item.get("url") or None  # Notion url field rejects empty string
        payload = {
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": {
                "タイトル": {
                    "title": [{"text": {"content": item.get("title", "")[:2000]}}]
                },
                "ネタ元URL": {"url": url_value},
                "バズスコア": {"number": float(item.get("score", 0))},
                "投稿案A": {
                    "rich_text": [{"text": {"content": item.get("pattern_a", "")[:2000]}}]
                },
                "投稿案B": {
                    "rich_text": [{"text": {"content": item.get("pattern_b", "")[:2000]}}]
                },
                "投稿案C": {
                    "rich_text": [{"text": {"content": item.get("pattern_c", "")[:2000]}}]
                },
                "ステータス": {"select": {"name": "確認待ち"}},
                "収集日": {"date": {"start": today}},
            },
        }

        try:
            resp = requests.post(
                "https://api.notion.com/v1/pages",
                headers=headers,
                json=payload,
                timeout=30,
            )
            resp.raise_for_status()
            print("      ✅ 保存完了")
            saved += 1
        except requests.RequestException as exc:
            print(f"      ❌ Notion保存エラー: {exc}")
            if hasattr(exc, "response") and exc.response is not None:
                print(f"         詳細: {exc.response.text[:300]}")

    return saved


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(total: int, scored: list[dict], saved: int) -> None:
    print("\n" + "=" * 50)
    print("📊 実行結果サマリー")
    print("=" * 50)
    print(f"  収集件数   : {total} 件")
    print(f"  スコア通過 : {len(scored)} 件（7点以上）")
    print(f"  Notion保存 : {saved} 件")

    if scored:
        print("\n【生成された投稿案一覧】")
        for item in scored:
            print(f"\n{'─' * 50}")
            print(f"📌 {item.get('title', '')}")
            print(f"   スコア : {item.get('score', 0)}/10 — {item.get('score_reason', '')}")
            print(f"   URL    : {item.get('url', '')}")
            for label, key in [("A ノウハウ暴露型", "pattern_a"),
                                ("B 業界批評型", "pattern_b"),
                                ("C 数字報告型", "pattern_c")]:
                text = item.get(key, "")
                print(f"\n   [{label}]  {len(text)}文字")
                print(f"   {text}")

    print(f"\n{'=' * 50}")
    print("✨ 完了！Notionデータベースを確認してください。")
    print("=" * 50)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 50)
    print("🤖 AI情報収集 & X投稿案生成ツール")
    print("=" * 50)

    validate_env()

    # STEP 1
    all_items = gather_news()
    if not all_items:
        print("❌ 情報が取得できませんでした。終了します。")
        sys.exit(1)

    # STEP 2
    scored_items = score_items(all_items)
    if not scored_items:
        print("\n⚠️  7点以上のネタがありませんでした。終了します。")
        sys.exit(0)

    # STEP 3
    items_with_posts = generate_posts(scored_items)
    if not items_with_posts:
        print("\n❌ 投稿文の生成に失敗しました。終了します。")
        sys.exit(1)

    # STEP 4
    saved_count = save_to_notion(items_with_posts)

    print_summary(len(all_items), items_with_posts, saved_count)


if __name__ == "__main__":
    main()
