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

SCORE_THRESHOLD = 7.0
MAX_POST_CHARS = 140
BEST_POSTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "best_posts.txt")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_json(text: str) -> dict:
    """Extract and parse the first JSON object found in *text*."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"JSON not found in response:\n{text[:300]}")
    return json.loads(match.group())


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def load_best_posts() -> str:
    """Load the author's best past posts (separated by ---, # lines are comments)."""
    if not os.path.exists(BEST_POSTS_FILE):
        return ""
    with open(BEST_POSTS_FILE, encoding="utf-8") as f:
        lines = [line for line in f.read().splitlines() if not line.lstrip().startswith("#")]
    posts = [p.strip() for p in "\n".join(lines).split("---") if p.strip()]
    return "\n\n".join(f"例{i}:\n{p}" for i, p in enumerate(posts, 1))


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
        "各件：タイトル・URL・3行要約・Xで使えるポイントをJSON形式で返すこと\n"
        "URLは実際に参照した記事のものだけを使い、推測で作らないこと。要約には記事中の数字をそのまま残すこと\n\n"
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
        "search_recency_filter": "day",
    }

    try:
        resp = requests.post(
            "https://api.perplexity.ai/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
        data = parse_json(content)
        items = data.get("items", [])
    except requests.RequestException as exc:
        print(f"❌ Perplexity APIエラー: {exc}")
        sys.exit(1)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"❌ レスポンス解析エラー: {exc}")
        sys.exit(1)

    # Keep only URLs that Perplexity actually cited; the model may invent the rest.
    cited = {normalize_url(u) for u in body.get("citations", []) if isinstance(u, str)}
    cited |= {
        normalize_url(r["url"]) for r in body.get("search_results", [])
        if isinstance(r, dict) and r.get("url")
    }
    if cited:
        for item in items:
            url = item.get("url") or ""
            if url and normalize_url(url) not in cited:
                print(f"   ⚠️  引用元に無いURLを除外: {url[:60]}")
                item["url"] = ""

    print(f"✅ {len(items)} 件の情報を収集しました")
    for idx, item in enumerate(items, 1):
        print(f"   {idx}. {item.get('title', '(タイトルなし)')[:60]}")

    return items


def remove_duplicates(items: list[dict]) -> list[dict]:
    """Skip items whose URL is already saved in the Notion database."""
    fresh = []
    for item in items:
        url = item.get("url")
        if not url:
            fresh.append(item)
            continue
        try:
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
                headers=NOTION_HEADERS,
                json={"filter": {"property": "ネタ元URL", "url": {"equals": url}}, "page_size": 1},
                timeout=30,
            )
            resp.raise_for_status()
            exists = bool(resp.json().get("results"))
        except requests.RequestException as exc:
            print(f"   ⚠️  重複チェック失敗（そのまま続行）: {exc}")
            exists = False
        if exists:
            print(f"   ⏭️  保存済みのためスキップ: {item.get('title', '')[:45]}")
        else:
            fresh.append(item)
    return fresh


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
            "採点基準（各1〜10点の整数）:\n"
            "1. buzz: バズ可能性（驚き・共感・反論したくなる度）\n"
            "2. exec: 従業員10〜50名の中小企業の社長が「自分ごと」と感じる度\n"
            "3. fit: AIエージェント会社CEO × LINEマーケ専門家が語る必然性\n\n"
            "目安: 5点=よくある話題、7点=明日の経営判断に効く具体性がある、"
            "9点以上=業界の常識が変わるレベル。迷ったら低い方を付けること。\n\n"
            "以下のJSON形式のみで返すこと:\n"
            '{"buzz": <1-10>, "exec": <1-10>, "fit": <1-10>, "reason": "採点理由を1〜2文で"}'
        )

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            data = parse_json(resp.content[0].text)
            axes = [float(data[k]) for k in ("buzz", "exec", "fit")]
            score = round(sum(axes) / len(axes), 1)
            reason = data.get("reason", "")
        except Exception as exc:
            print(f"      ⚠️  スコアリングスキップ: {exc}")
            continue

        item["score"] = score
        item["score_reason"] = reason
        badge = "✅ 通過" if score >= SCORE_THRESHOLD else "❌ スキップ"
        print(f"      スコア: {score}/10  {badge}  — {reason[:60]}")

        if score >= SCORE_THRESHOLD:
            passing.append(item)

    print(f"\n   📊 {len(items)} 件中 {len(passing)} 件が{SCORE_THRESHOLD:g}点以上で通過")
    return passing


# ---------------------------------------------------------------------------
# STEP 3: Claude — post generation
# ---------------------------------------------------------------------------

POST_KEYS = [("A", "pattern_a"), ("B", "pattern_b"), ("C", "pattern_c")]


def build_post_prompt(item: dict, best_posts: str) -> str:
    examples = best_posts or "（未設定。best_posts.txt に伸びた過去投稿を入れると精度が上がります）"
    return (
        "あなたは「AI経営便り」を運営するAIエージェント会社CEO（LINEマーケ専門）としてXに投稿します。\n\n"
        "# 読者\n"
        "従業員10〜50名の中小企業の社長。人手不足と集客に悩み、AIに興味はあるが「何からやればいいか」が分からない。\n\n"
        "# ネタ\n"
        f"タイトル: {item.get('title', '')}\n"
        f"要約: {item.get('summary', '')}\n"
        f"Xポイント: {item.get('x_points', '')}\n\n"
        "# お手本（発信者本人の伸びた過去投稿。口調と構成を真似る。内容は真似ない）\n"
        f"{examples}\n\n"
        "# 3つの型\n"
        "A ノウハウ暴露型: 「明日からできる手順3つ」のように具体的なやり方を箇条書きで。保存されることを狙う。\n"
        "B 業界批評型: 「みんなやっているが実は損をしていること」を指摘し、自分の立場をはっきり示す。"
        "リプライで議論が起きることを狙う。特定の個人や会社は攻撃しない。\n"
        "C 数字報告型: ネタの中の数字を主役にし、「その数字が中小企業にとって何を意味するか」を一言で言い切る。\n\n"
        "# 書き方のルール\n"
        "- 1行目は次のどれかで始める: 意外な事実 / やらないと損すること / 常識の否定 / 固有名詞＋数字\n"
        "- 必ず「だから中小企業はこうする」という具体的な一手を入れる\n"
        "- 締めは、答えやすい二択の問いかけ、または保存を促す一言\n"
        "- 数字はネタに書かれているものだけ使う。自社の実績を入れたい場所は【自社数値】と書いて空欄にする\n"
        f"- {MAX_POST_CHARS}文字以内。ハッシュタグとURLは入れない。改行で読みやすくする\n"
        "- 「いかがでしょうか」「注目です」などの定型句は使わない\n\n"
        "# 手順\n"
        "各型で2案ずつ書き、読者が「自分のことだ」と感じる度合いが高い方を各型1案選んで返す。\n\n"
        "# 出力（JSONのみ）\n"
        '{"pattern_a": "...", "pattern_b": "...", "pattern_c": "...", '
        '"hook_a": "使った1行目の型", "hook_b": "...", "hook_c": "..."}'
    )


def shorten_posts(client: anthropic.Anthropic, item: dict) -> None:
    """Ask Claude once to shorten any pattern that exceeds the character limit."""
    over = {key: item[key] for _, key in POST_KEYS if len(item[key]) > MAX_POST_CHARS}
    if not over:
        return
    prompt = (
        f"以下のX投稿を、意味と1行目のフックを保ったまま各{MAX_POST_CHARS}文字以内に短くしてください。\n"
        "数字や【自社数値】はそのまま残すこと。\n\n"
        f"{json.dumps(over, ensure_ascii=False)}\n\n"
        "同じキーのJSONのみで返すこと。"
    )
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        data = parse_json(resp.content[0].text)
    except Exception as exc:
        print(f"      ⚠️  短縮スキップ: {exc}")
        return
    for key in over:
        if data.get(key):
            item[key] = data[key]


def generate_posts(items: list[dict]) -> list[dict]:
    print_section("✍️ ", "STEP 3: Claudeで投稿文を生成中...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    best_posts = load_best_posts()
    if not best_posts:
        print("   ⚠️  best_posts.txt にお手本がありません（汎用の文体で生成します）")
    results = []

    for idx, item in enumerate(items, 1):
        title_short = item.get("title", "")[:45]
        print(f"   [{idx}/{len(items)}] {title_short}...")

        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1500,
                messages=[{"role": "user", "content": build_post_prompt(item, best_posts)}],
            )
            data = parse_json(resp.content[0].text)
            for label, key in POST_KEYS:
                item[key] = data.get(key, "")
                item[f"hook_{label.lower()}"] = data.get(f"hook_{label.lower()}", "")
        except Exception as exc:
            print(f"      ⚠️  生成スキップ: {exc}")
            continue

        shorten_posts(client, item)

        for label, key in POST_KEYS:
            count = len(item[key])
            flag = "✅" if count <= MAX_POST_CHARS else "⚠️ 超過"
            print(f"      パターン{label}: {count}文字 {flag}  フック: {item[f'hook_{label.lower()}']}")

        results.append(item)

    return results


# ---------------------------------------------------------------------------
# STEP 4: Notion — save
# ---------------------------------------------------------------------------

def save_to_notion(items: list[dict]) -> int:
    print_section("📝", "STEP 4: Notionに保存中...")

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
                headers=NOTION_HEADERS,
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

    all_items = remove_duplicates(all_items)
    if not all_items:
        print("\n⚠️  新しいネタがありませんでした（すべて保存済み）。終了します。")
        sys.exit(0)

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
