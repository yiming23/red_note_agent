"""Telegram bot — pushes candidates and routes user feedback.

Two halves:

1. **Sending side** (`send_candidate`, `send_revision`):
   Synchronous HTTP POST via httpx to Telegram's bot HTTP API. Used by the
   orchestration pipeline (which is sync).

2. **Receiving side** (`build_bot_app`, `run_bot_polling`):
   Long-polling Application from python-telegram-bot. Handles:
   - Inline button presses [✅ 发布] / [❌ 跳过]
   - Free-text replies → triggers rewrite_agent → pushes new version

The two halves are separate processes in deployment:
   - `scripts/run_pipeline_once.py` (or scheduler) does the sending
   - `scripts/run_telegram_bot.py` runs the polling loop forever

DESIGN.md § 9.
"""

from __future__ import annotations

import asyncio
import json as json_module
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx

from xhs_agent.agents.rewrite_agent import rewrite as run_rewrite_agent
from xhs_agent.config import settings
from xhs_agent.domain.games import GAMES_DOMAIN
from xhs_agent.observability.logger import get_logger
from xhs_agent.storage.db import session_scope
from xhs_agent.storage.models import Post, PostState
from xhs_agent.storage.repositories import PostRepository
from xhs_agent.utils.formatter import FormattedPost, format_post

log = get_logger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"

CB_APPROVE = "approve"
CB_REJECT = "reject"

# Pending /gen confirmations: chat_id → {candidates: [(appid, name), ...], direction: str}
_pending_gen: dict[int, dict] = {}


# ============================================================
# Sending side (synchronous, no python-telegram-bot dependency)
# ============================================================


@dataclass
class _SendResult:
    success: bool
    telegram_message_id: Optional[int]
    error: Optional[str] = None


def send_candidate_with_images(
    post: Post,
    formatted: FormattedPost,
    signal_summary: str,
    pages: list[dict],
) -> _SendResult:
    """Push a candidate as a Telegram media group (images) + a button message.

    Telegram does not support inline keyboards on media groups, so we:
    1. Send up to 10 images as sendMediaGroup (first image gets caption)
    2. Send a plain text message with the inline keyboard buttons
    The button message ID is recorded for reply mapping.
    """
    if not settings.telegram_push_enabled:
        log.info("telegram_push_disabled", post_id=post.id)
        return _SendResult(success=True, telegram_message_id=None, error="push_disabled")

    if settings.telegram_dry_run:
        log.info("telegram_dry_run_media_group", post_id=post.id, pages=len(pages))
        return _SendResult(success=True, telegram_message_id=None, error="dry_run")

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.error("telegram_missing_credentials")
        return _SendResult(success=False, telegram_message_id=None, error="missing_credentials")

    image_paths = [p["image_path"] for p in pages if p.get("image_path")]
    if not image_paths:
        # No images rendered — fall back to text-only
        return send_candidate(post, formatted, signal_summary)

    # 1. Send media group
    caption = f"🎮 候选 #{post.id} — {post.trigger_entity_name or ''}"
    _send_media_group_sync(image_paths[:10], caption=caption)

    # 2. Send button message and record its ID
    text = _build_candidate_message(post, formatted, signal_summary)
    keyboard = _build_inline_keyboard(post.id)
    return _send_message_sync(text=text, reply_markup=keyboard)


def send_candidate(post: Post, formatted: FormattedPost, signal_summary: str) -> _SendResult:
    """Push a freshly generated candidate to Telegram with inline buttons.

    Updates `post.telegram_message_id` on success (caller must commit).
    """
    if not settings.telegram_push_enabled:
        log.info("telegram_push_disabled", post_id=post.id)
        return _SendResult(success=True, telegram_message_id=None, error="push_disabled")

    if settings.telegram_dry_run:
        log.info(
            "telegram_dry_run",
            post_id=post.id,
            title=formatted.title,
            content_chars=len(formatted.content),
        )
        return _SendResult(success=True, telegram_message_id=None, error="dry_run")

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        log.error("telegram_missing_credentials")
        return _SendResult(success=False, telegram_message_id=None, error="missing_credentials")

    text = _build_candidate_message(post, formatted, signal_summary)
    keyboard = _build_inline_keyboard(post.id)

    return _send_message_sync(text=text, reply_markup=keyboard)


def send_revision(post: Post, formatted: FormattedPost, iteration_idx: int) -> _SendResult:
    """Push a rewritten version after user feedback. Same buttons as the first push."""
    if not settings.telegram_push_enabled or settings.telegram_dry_run:
        log.info("telegram_revision_skipped", post_id=post.id, dry_run=settings.telegram_dry_run)
        return _SendResult(success=True, telegram_message_id=None, error="dry_run_or_disabled")

    text = _build_revision_message(post, formatted, iteration_idx)
    keyboard = _build_inline_keyboard(post.id)
    return _send_message_sync(text=text, reply_markup=keyboard)


def send_revision_with_images(
    post: Post,
    formatted: FormattedPost,
    iteration_idx: int,
    pages: list[dict],
) -> _SendResult:
    """Push a rewritten version with a refreshed media group.

    Mirrors `send_candidate_with_images`: objective chart images are reused
    as-is (their `image_path` is untouched by the rewrite), subjective cards
    (cover / combined_summary / recommendation) carry freshly rendered images.
    Falls back to text-only `send_revision` if no images are available.
    """
    if not settings.telegram_push_enabled or settings.telegram_dry_run:
        log.info("telegram_revision_skipped", post_id=post.id, dry_run=settings.telegram_dry_run)
        return _SendResult(success=True, telegram_message_id=None, error="dry_run_or_disabled")

    from pathlib import Path

    image_paths = []
    for p in sorted(pages, key=lambda p: p.get("page", 0)):
        ip = p.get("image_path")
        if ip and Path(ip).exists():
            image_paths.append(ip)
        else:
            log.warning("revision_image_missing_skipped", post_id=post.id,
                        page=p.get("page"), type=p.get("type"))

    if not image_paths:
        return send_revision(post, formatted, iteration_idx)

    caption = f"🔄 候选 #{post.id} 已按反馈更新"
    _send_media_group_sync(image_paths[:10], caption=caption)

    text = _build_revision_message(post, formatted, iteration_idx)
    keyboard = _build_inline_keyboard(post.id)
    return _send_message_sync(text=text, reply_markup=keyboard)


def _send_clean_copy(post_id: int) -> None:
    """Send images + clean plain-text copy after approval, for easy copy-paste."""
    with session_scope() as s:
        post = PostRepository(s).get(post_id)
        if not post:
            return
        title = post.title or ""
        content = post.content or ""
        hashtags: list[str] = post.hashtags or []
        pages: list[dict] = post.pages or []

    # 1. Re-send images (no caption, no buttons)
    image_paths = [p["image_path"] for p in pages if p.get("image_path")]
    if image_paths:
        _send_media_group_sync(image_paths[:10], caption="")

    # 2. Send clean text: title / content / hashtags — nothing else
    tags_line = " ".join(f"#{t.lstrip('#')}" for t in hashtags)
    clean_text = f"{title}\n\n{content}"
    if tags_line:
        clean_text += f"\n\n{tags_line}"

    _send_message_sync(text=clean_text, reply_markup=None)
    log.info("clean_copy_sent", post_id=post_id)


def send_plain(message: str) -> _SendResult:
    """Send a non-candidate message (status update, error report)."""
    if not settings.telegram_push_enabled or settings.telegram_dry_run:
        log.info("telegram_plain_skipped", message_preview=message[:60])
        return _SendResult(success=True, telegram_message_id=None, error="dry_run_or_disabled")
    return _send_message_sync(text=message, reply_markup=None)


def _send_message_sync(text: str, reply_markup: Optional[dict]) -> _SendResult:
    url = TELEGRAM_API_BASE.format(token=settings.telegram_bot_token, method="sendMessage")
    payload: dict = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = json_module.dumps(reply_markup)

    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(url, data=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        log.error("telegram_send_failed", error=str(e))
        return _SendResult(success=False, telegram_message_id=None, error=str(e))

    if not data.get("ok"):
        log.error("telegram_send_not_ok", response=data)
        return _SendResult(success=False, telegram_message_id=None, error=str(data))

    msg_id = data.get("result", {}).get("message_id")
    log.info("telegram_send_ok", message_id=msg_id)
    return _SendResult(success=True, telegram_message_id=msg_id)


def _send_media_group_sync(image_paths: list[str], caption: str = "") -> None:
    """POST sendMediaGroup with local image files. Errors are logged, not raised."""
    url = TELEGRAM_API_BASE.format(token=settings.telegram_bot_token, method="sendMediaGroup")
    media = []
    for i, path in enumerate(image_paths):
        item: dict = {"type": "photo", "media": f"attach://img{i}"}
        if i == 0 and caption:
            item["caption"] = caption[:1024]  # Telegram caption limit
        media.append(item)

    files = {f"img{i}": open(p, "rb") for i, p in enumerate(image_paths)}
    try:
        payload = {
            "chat_id": settings.telegram_chat_id,
            "media": json_module.dumps(media),
        }
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, data=payload, files=files)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                log.error("telegram_media_group_not_ok", response=data)
            else:
                log.info("telegram_media_group_sent", count=len(image_paths))
    except Exception as exc:
        log.error("telegram_media_group_failed", error=str(exc))
    finally:
        for f in files.values():
            f.close()


# ============================================================
# Message rendering
# ============================================================


def _build_candidate_message(post: Post, fp: FormattedPost, signal_summary: str) -> str:
    # Resolve Chinese content_type display name from template registry
    template = next(
        (t for t in GAMES_DOMAIN.templates if t.name == post.template_used),
        None,
    )
    content_type_zh = template.content_type if template else (post.template_used or "?")

    # v5 status line — quick sanity check at top
    status_bits = [
        f"content_type: {content_type_zh}",
        f"标题: {len(fp.title)} 字" + (" ⚠️" if fp.title_issues else " ✓"),
        f"hashtag: {len(fp.hashtags)} 个" + (" ⚠️" if fp.hashtag_issues else " ✓"),
    ]
    status_line = " | ".join(status_bits)

    # Combine all warnings/issues into one block
    warning_lines: list[str] = []
    warning_lines.extend(fp.title_issues)
    warning_lines.extend(fp.hashtag_issues)
    warning_lines.extend(fp.warnings)
    for banned, replacement in fp.rewrites_applied:
        warning_lines.append(f"已自动替换『{banned}』→『{replacement}』")
    for word, cat in fp.illegal_hits:
        warning_lines.append(f"❌ 违禁词命中: {word} [{cat}]")

    warnings_block = ""
    if warning_lines:
        warnings_block = "\n\n⚠️ 检查报告\n" + "\n".join(f"  • {w}" for w in warning_lines)

    return (
        f"🎮 候选 #{post.id}\n"
        f"{status_line}\n"
        f"{signal_summary}\n"
        f"{'─' * 26}\n\n"
        f"{fp.full_text}\n"
        f"{warnings_block}\n\n"
        f"{'─' * 26}\n"
        f"💬 想反馈？长按这条消息 → Reply → 输入意见（精确指向 #{post.id}）\n"
        f"   或直接发文字（默认指向最近一条 pending 候选）\n"
        f"或点下方按钮直接 ✅ / ❌"
    ).strip()


def _build_revision_message(post: Post, fp: FormattedPost, iteration_idx: int) -> str:
    return (
        f"🔁 候选 #{post.id} - 修订版 v{iteration_idx + 1}\n"
        f"{'─' * 26}\n\n"
        f"{fp.full_text}\n\n"
        f"{'─' * 26}\n"
        f"💬 还想改？再回复一句\n"
        f"或点下方按钮收尾"
    ).strip()


def _build_inline_keyboard(post_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ 发布", "callback_data": f"{CB_APPROVE}:{post_id}"},
                {"text": "❌ 跳过", "callback_data": f"{CB_REJECT}:{post_id}"},
            ]
        ]
    }


# ============================================================
# Receiving side (python-telegram-bot Application)
# ============================================================


def build_bot_app():
    """Construct the Application with handlers. Caller runs polling.

    Lazy import of python-telegram-bot so the sending side doesn't pull it in.
    """
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")

    from telegram.ext import (
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("status", _cmd_status))
    app.add_handler(CommandHandler("gen", _cmd_gen))
    app.add_handler(CallbackQueryHandler(_on_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text_reply)
    )
    app.add_handler(MessageHandler(filters.PHOTO, _on_photo_upload))
    app.add_error_handler(_on_error)
    return app


def run_bot_polling() -> None:
    """Blocking entry point for the bot worker process."""
    app = build_bot_app()
    log.info("telegram_bot_polling_start")
    app.run_polling(drop_pending_updates=True)


# ----------------------------------------------------------------
# Handlers (async — required by python-telegram-bot v21)
# ----------------------------------------------------------------


async def _cmd_start(update, context) -> None:
    if not _is_authorized(update):
        return
    await update.effective_chat.send_message(
        "Bot 已就绪。Pipeline 跑出候选会推到这里。\n"
        "/status — 查看今日预算和待审帖数\n"
        "回复任意候选 = 用反馈重写"
    )


async def _cmd_gen(update, context) -> None:
    """Handle /gen command. Supports two formats:
      /gen 《游戏名》 写作方向
      /gen 游戏名
      写作方向（game name = first line, direction = rest）

    Searches Steam for candidates and asks user to confirm before generating.
    """
    if not _is_authorized(update):
        return

    import re as _re
    text = (update.message.text or "").replace("/gen", "", 1).strip()
    if not text:
        await update.message.reply_text(
            "格式：\n"
            "/gen 《游戏名》 写作方向\n"
            "或\n"
            "/gen 游戏名\n写作方向（第一行=游戏名，后面=方向）"
        )
        return

    # Parse game name and direction
    match = _re.search(r'[《<](.+?)[》>]\s*(.*)', text, _re.DOTALL)
    if match:
        game_name = match.group(1).strip()
        direction = match.group(2).strip() or "综合评测分析"
    else:
        parts = text.split('\n', 1)
        game_name = parts[0].strip()
        direction = parts[1].strip() if len(parts) > 1 else "综合评测分析"

    await update.message.reply_text(f"🔍 搜索中：{game_name}…")

    chat_id = update.effective_chat.id
    try:
        from xhs_agent.orchestration.manual_pipeline import search_games
        candidates = await asyncio.to_thread(search_games, game_name, 5)
    except Exception as exc:
        log.error("cmd_gen_search_failed", error=str(exc))
        await update.message.reply_text(f"❌ Steam 搜索失败：{exc}")
        return

    if not candidates:
        await update.message.reply_text(f"❌ 找不到《{game_name}》，试试更完整的名字？")
        return

    if len(candidates) == 1:
        # Only one result — proceed directly
        appid, name = candidates[0]
        await update.message.reply_text(
            f"✅ 找到：{name}\n📝 正在生成，方向：{direction[:60]}\n预计 1-2 分钟…"
        )
        _run_gen(update, chat_id, appid, name, direction)
        return

    # Multiple results — let user pick
    _pending_gen[chat_id] = {"candidates": candidates, "direction": direction}
    lines = [f"找到 {len(candidates)} 个结果，回复序号确认：\n"]
    for i, (appid, name) in enumerate(candidates, 1):
        lines.append(f"{i}. {name}  (appid {appid})")
    lines.append("\n回复 1-5 选择，或 /gen 重新搜索")
    await update.message.reply_text("\n".join(lines))


def _run_gen(update, chat_id: int, appid: str, game_name: str, direction: str) -> None:
    """Fire-and-forget: run manual pipeline in a background thread."""
    import threading
    from xhs_agent.orchestration.manual_pipeline import run_manual_pipeline, _collect_entity

    def _worker():
        try:
            entity = _collect_entity(appid)
            if entity is None:
                send_plain(f"❌ 数据获取失败（appid={appid}），请稍后重试")
                return
            # Override name with what Steam confirmed
            entity.name = game_name
            # Pass full direction through to ContentAgent prompt
            entity._user_direction = direction
            from xhs_agent.orchestration.manual_pipeline import (
                _make_judgment, _summarize_available_data,
            )
            from xhs_agent.agents.content_director import direct_content, ContentPlan
            from xhs_agent.domain.games.collectors.duckduckgo import DuckDuckGoCollector
            from xhs_agent.agents.opinion_miner import mine_opinions
            from xhs_agent.domain.games.collectors.similar_games import SimilarGamesCollector
            from xhs_agent.agents.review_miner import extract_themes
            from xhs_agent.agents.buy_or_wait import BuyRecommendation, analyze as analyze_buy_or_wait
            from xhs_agent.agents.compliance_guard import check as compliance_check
            from xhs_agent.agents.content_agent import generate_content
            from xhs_agent.budget.guard import BudgetExceededError
            from xhs_agent.domain.games import GAMES_DOMAIN
            from xhs_agent.processors.page_builder import build_pages_from_plan
            from xhs_agent.processors.playtime_buckets import compute_buckets
            from xhs_agent.visualization import render_all_pages
            from xhs_agent.storage.db import session_scope
            from xhs_agent.storage.models import PostState
            from xhs_agent.storage.repositories import PostRepository
            from xhs_agent.utils.formatter import format_post

            available_data = _summarize_available_data(entity)
            try:
                content_plan = direct_content(
                    game_name=entity.name,
                    user_direction=direction,
                    available_data=available_data,
                )
            except Exception as exc:
                content_plan = ContentPlan(
                    article_template="negative_review_burst",
                    key_narrative=direction[:50],
                    search_queries=[entity.name],
                    charts_needed=[],
                )

            if content_plan.search_queries:
                try:
                    entity.external_opinions = DuckDuckGoCollector().search(content_plan.search_queries)
                    if entity.external_opinions:
                        entity.key_viewpoints = mine_opinions(entity.external_opinions, entity.name, "DuckDuckGo搜索")
                        entity._sources_label = "DuckDuckGo搜索"
                except Exception:
                    pass

            try:
                SimilarGamesCollector().enrich_entity(entity)
            except Exception:
                pass

            theme_summary = None
            if entity.recent_review_pool:
                try:
                    theme_summary = extract_themes(pool=entity.recent_review_pool, appid=entity.appid, use_cache=True)
                except Exception:
                    pass
            buckets = compute_buckets(entity) if entity.recent_review_pool else None

            try:
                buy_rec = analyze_buy_or_wait(entity=entity, theme_summary=theme_summary, playtime_buckets=buckets)
            except Exception:
                buy_rec = BuyRecommendation(rating="C", one_sentence="数据不足，建议先观望", success=False)

            synthetic_judgment = _make_judgment(content_plan, entity)
            template_obj = next((t for t in GAMES_DOMAIN.templates if t.name == content_plan.article_template), GAMES_DOMAIN.templates[0])

            try:
                generated = generate_content(judgment=synthetic_judgment, entity=entity, domain=GAMES_DOMAIN, theme_summary=theme_summary, playtime_buckets=buckets)
            except BudgetExceededError as exc:
                send_plain(f"⚠️ 预算超限：{exc}")
                return
            except Exception as exc:
                send_plain(f"❌ 文章生成失败：{exc}")
                return

            if not generated.success:
                send_plain(f"❌ 文章生成输出无效：{generated.error_message}")
                return

            content_type_zh = template_obj.content_type
            formatted = format_post(title=generated.title, content=generated.content, hashtags=generated.hashtags, content_type=content_type_zh, game_name=entity.name, genres=list(entity.genres) if entity.genres else [])
            _, clean_content, compliance_report = compliance_check(title=formatted.title, content=formatted.content, hashtags=formatted.hashtags, game_name=entity.name, buy_rec=buy_rec)
            if compliance_report.rewrites:
                formatted = format_post(title=formatted.title, content=clean_content, hashtags=formatted.hashtags, content_type=content_type_zh, game_name=entity.name, genres=list(entity.genres) if entity.genres else [])

            pages = build_pages_from_plan(title=formatted.title, content=formatted.content, entity=entity, buy_rec=buy_rec, content_plan=content_plan, theme_summary=theme_summary, playtime_buckets=buckets)
            try:
                pages = render_all_pages(entity=entity, buy_rec=buy_rec, pages=pages, theme_summary=theme_summary, playtime_buckets=buckets, title=formatted.title)
            except Exception:
                pass

            total_cost = generated.cost_usd
            themes_dict = {t.theme: t.share_pct for t in theme_summary.themes} if theme_summary and theme_summary.themes else None

            with session_scope() as s:
                post = PostRepository(s).create(
                    title=formatted.title, content=formatted.content, hashtags=formatted.hashtags,
                    template_used=generated.template_name, domain=GAMES_DOMAIN.name,
                    trigger_entity_id=entity.appid, trigger_entity_name=entity.name,
                    trigger_signals=["manual_gen"], source_urls=[entity.store_url] if entity.store_url else None,
                    used_meme_phrases=None, used_exemplar_ids=None, state=PostState.PENDING,
                    llm_cost_usd=total_cost, pipeline_run_id=None, content_type=content_type_zh,
                    pages=pages, cover_text=pages[0]["body"] if pages else None,
                    buy_rating=buy_rec.rating, suitable_for=buy_rec.suitable_for,
                    not_suitable_for=buy_rec.not_suitable_for, key_risks=buy_rec.key_risks,
                    wait_for=buy_rec.wait_for, themes_summary=themes_dict,
                    playtime_buckets_json=buckets.as_dict() if buckets else None,
                )
                s.expunge(post)

            direction_preview = direction[:200] + ("…" if len(direction) > 200 else "")
            signal_summary = (
                f"🎮 手动生成：{entity.name}\n📝 方向：{direction_preview}\n"
                f"📐 模板：{content_type_zh}\n💡 叙事：{content_plan.key_narrative}\n"
            )
            if buy_rec and buy_rec.success:
                signal_summary += buy_rec.format_for_telegram()

            send_candidate_with_images(post, formatted, signal_summary, pages)
            log.info("manual_gen_done", game=entity.name)

        except Exception as exc:
            log.error("manual_gen_worker_crashed", error=str(exc))
            send_plain(f"❌ 生成崩溃：{exc}")

    threading.Thread(target=_worker, daemon=True).start()


async def _cmd_status(update, context) -> None:
    if not _is_authorized(update):
        return
    from xhs_agent.budget.guard import get_status

    budget = get_status()
    pending_count = _count_pending_posts()
    msg = (
        f"📊 状态\n"
        f"今日 LLM 花费：${budget['spent_today_usd']} / ${budget['budget_usd']} ({budget['pct_used']}%)\n"
        f"待审候选：{pending_count} 篇"
    )
    await update.effective_chat.send_message(msg)


async def _on_callback(update, context) -> None:
    if not _is_authorized(update):
        return
    query = update.callback_query
    await query.answer()  # acknowledge so Telegram stops the spinner

    data = query.data or ""
    try:
        action, post_id_str = data.split(":", 1)
        post_id = int(post_id_str)
    except (ValueError, AttributeError):
        log.warning("telegram_callback_bad_data", data=data)
        return

    if action == CB_APPROVE:
        with session_scope() as s:
            PostRepository(s).update_state(post_id, PostState.APPROVED)
        await query.edit_message_reply_markup(reply_markup=None)
        await update.effective_chat.send_message(f"✅ 候选 #{post_id} 已发布，发送正文和图片…")
        log.info("telegram_post_approved", post_id=post_id)

        # Send clean copy: images first, then plain text for easy copy-paste
        await asyncio.to_thread(_send_clean_copy, post_id)

    elif action == CB_REJECT:
        with session_scope() as s:
            PostRepository(s).update_state(post_id, PostState.REJECTED)
        await query.edit_message_reply_markup(reply_markup=None)
        await update.effective_chat.send_message(f"❌ 候选 #{post_id} 已跳过")
        log.info("telegram_post_rejected", post_id=post_id)

    else:
        log.warning("telegram_callback_unknown_action", action=action)


async def _on_text_reply(update, context) -> None:
    """Free-text reply handler.

    Priority:
      1. If there's a pending /gen game-selection for this chat, handle the number pick.
      2. Otherwise treat as post rewrite feedback.
    """
    if not _is_authorized(update):
        return

    feedback = (update.message.text or "").strip()
    if not feedback:
        return

    chat_id = update.effective_chat.id

    # ── 1. Pending game confirmation ──────────────────────────
    if chat_id in _pending_gen and feedback.isdigit():
        pending = _pending_gen.pop(chat_id)
        candidates: list = pending["candidates"]
        direction: str = pending["direction"]
        idx = int(feedback) - 1
        if 0 <= idx < len(candidates):
            appid, name = candidates[idx]
            await update.message.reply_text(
                f"✅ 确认：{name}\n📝 正在生成，方向：{direction[:60]}\n预计 1-2 分钟…"
            )
            _run_gen(update, chat_id, appid, name, direction)
        else:
            await update.message.reply_text(
                f"请回复 1-{len(candidates)} 之间的数字，或重新 /gen"
            )
        return

    # ── 2. Auto-gen if message looks like a game name ─────────
    # Short text (≤25 chars) with no rewrite-feedback keywords → treat as /gen
    _FEEDBACK_KEYWORDS = ("改", "修改", "不要", "换", "重写", "帮我", "太", "觉得", "能不能", "可以")
    is_reply_to = update.message.reply_to_message is not None
    looks_like_feedback = is_reply_to or any(kw in feedback for kw in _FEEDBACK_KEYWORDS) or len(feedback) > 25
    if not looks_like_feedback:
        await update.message.reply_text(f"🔍 搜索中：{feedback}…")
        try:
            from xhs_agent.orchestration.manual_pipeline import search_games
            candidates = await asyncio.to_thread(search_games, feedback, 5)
        except Exception as exc:
            await update.message.reply_text(f"❌ Steam 搜索失败：{exc}")
            return
        if not candidates:
            await update.message.reply_text(f"❌ 找不到《{feedback}》，试试更完整的名字？")
            return
        if len(candidates) == 1:
            appid, name = candidates[0]
            await update.message.reply_text(
                f"✅ 找到：{name}\n📝 正在生成…\n预计 1-2 分钟…"
            )
            _run_gen(update, chat_id, appid, name, "综合评测分析")
        else:
            _pending_gen[chat_id] = {"candidates": candidates, "direction": "综合评测分析"}
            lines = [f"找到 {len(candidates)} 个结果，回复序号确认：\n"]
            for i, (appid, name) in enumerate(candidates, 1):
                lines.append(f"{i}. {name}  (appid {appid})")
            lines.append("\n回复 1-5 选择，或重新发名字")
            await update.message.reply_text("\n".join(lines))
        return

    # ── 3. Post rewrite feedback ───────────────────────────────
    # Find which post this feedback applies to:
    # 1. If user replied-to a specific message, use that mapping
    # 2. Else use the most recent pending/iterating post
    post_id = _find_target_post(update)
    if post_id is None:
        await update.message.reply_text(
            "现在没有正在审的候选。等下次 pipeline 推帖再回复反馈～"
        )
        return

    await update.message.reply_text(f"收到反馈，对候选 #{post_id} 重写中…")

    # Run the rewrite (sync, may take a few seconds — fine for our scale)
    revised, iteration_idx, new_pages = await asyncio.to_thread(
        _rewrite_post_sync, post_id, feedback
    )

    if revised is None:
        await update.message.reply_text("⚠️ 重写失败，原候选保持不变。")
        return

    formatted = format_post(
        title=revised.title, content=revised.content, hashtags=revised.hashtags
    )
    if new_pages:
        res = send_revision_with_images(
            post=_load_post_lite(post_id), formatted=formatted,
            iteration_idx=iteration_idx, pages=new_pages,
        )
    else:
        res = send_revision(post=_load_post_lite(post_id), formatted=formatted, iteration_idx=iteration_idx)
    if res.success and res.telegram_message_id:
        with session_scope() as s:
            post = PostRepository(s).get(post_id)
            if post:
                post.telegram_message_id = res.telegram_message_id


async def _on_error(update, context) -> None:
    log.error("telegram_handler_error", error=str(context.error))


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------


def _is_authorized(update) -> bool:
    """Reject messages from any chat other than Yiming's configured one."""
    expected = str(settings.telegram_chat_id).strip()
    if not expected:
        return True  # not configured; permissive (dev only)
    actual = str(update.effective_chat.id) if update.effective_chat else ""
    if actual != expected:
        log.warning("telegram_unauthorized_chat", got=actual, expected=expected)
        return False
    return True


def _find_target_post(update) -> Optional[int]:
    """Map an incoming text message to a post_id."""
    # Reply-to mapping
    reply = update.message.reply_to_message if update.message else None
    if reply is not None:
        with session_scope() as s:
            post = PostRepository(s).get_by_telegram_message_id(reply.message_id)
            if post and post.state in (PostState.PENDING, PostState.ITERATING):
                return post.id

    # Default: latest pending/iterating
    with session_scope() as s:
        post = PostRepository(s).get_latest_pending()
        return post.id if post else None


def _count_pending_posts() -> int:
    from sqlalchemy import select

    from xhs_agent.storage.models import Post as P

    with session_scope() as s:
        stmt = select(P).where(P.state.in_([PostState.PENDING, PostState.ITERATING]))
        return len(s.scalars(stmt).all())


# Page types whose content/visuals are *subjective* (driven by buy_rec/title/
# narrative) — these get rebuilt + re-rendered on rewrite. Everything else is
# an *objective* data chart (price/trend/theme/similar-games/etc.) and keeps
# its originally-rendered image untouched.
_SUBJECTIVE_PAGE_TYPES = {"cover", "combined_summary_card", "recommendation"}


def _rewrite_post_sync(post_id: int, feedback: str):
    """Run rewrite_agent + buy_rec_rewriter, re-render subjective cards, record iteration.

    Returns (GeneratedContent, iteration_idx, new_pages) — new_pages is the full
    page list with subjective cards re-rendered and objective charts reused as-is,
    or None if rewrite failed / no pages exist on the post.
    """
    from types import SimpleNamespace

    from xhs_agent.agents.buy_or_wait import BuyRecommendation
    from xhs_agent.agents.buy_rec_rewriter import revise as revise_buy_rec
    from xhs_agent.processors.page_builder import (
        _combined_summary_page,
        _cover_page,
        _recommendation_page,
    )
    from xhs_agent.visualization import render_all_pages

    with session_scope() as s:
        post = PostRepository(s).get(post_id)
        if post is None:
            return None, 0, None

        revised = run_rewrite_agent(
            original_title=post.title,
            original_content=post.content,
            original_hashtags=post.hashtags or [],
            feedback=feedback,
            template_name=post.template_used,
            domain=GAMES_DOMAIN,
            game_name=post.trigger_entity_name,
        )

        if not revised.success:
            log.error("rewrite_failed", post_id=post_id, error=revised.error_message)
            return None, 0, None

        total_cost = revised.cost_usd

        # ── Re-derive the subjective verdict (rating/one_sentence/risks/...)
        #    so it matches the new direction while staying anchored to the
        #    frozen objective facts — then rebuild + re-render only the
        #    subjective cards (cover / combined_summary / recommendation).
        new_pages: Optional[list[dict]] = None
        old_pages = list(post.pages or [])
        snapshot = post.objective_snapshot or {}
        if old_pages and snapshot:
            original_buy_rec = BuyRecommendation(
                rating=post.buy_rating or "C",
                one_sentence=post.cover_text or "",
                suitable_for=list(post.suitable_for or []),
                not_suitable_for=list(post.not_suitable_for or []),
                key_risks=list(post.key_risks or []),
                wait_for=post.wait_for or "",
            )
            try:
                revised_buy_rec = revise_buy_rec(
                    original=original_buy_rec,
                    feedback=feedback,
                    objective_facts=snapshot,
                    game_name=post.trigger_entity_name or "",
                    revised_title=revised.title,
                    revised_content=revised.content,
                )
                total_cost += revised_buy_rec.cost_usd

                snap_entity = SimpleNamespace(**{
                    "appid": snapshot.get("appid"),
                    "name": snapshot.get("name") or post.trigger_entity_name,
                    "historical_positive_rate": snapshot.get("historical_positive_rate"),
                    "recent_7d_positive_rate": snapshot.get("recent_7d_positive_rate"),
                    "recent_7d_review_count": snapshot.get("recent_7d_review_count"),
                    "total_reviews": snapshot.get("total_reviews"),
                    "current_player_count": snapshot.get("current_player_count"),
                })

                rebuilt = list(old_pages)
                for i, p in enumerate(rebuilt):
                    ptype = p.get("type")
                    if ptype == "cover":
                        rebuilt[i] = _cover_page(snap_entity, revised_buy_rec, revised.title, revised.content,
                                                 cover_image_path=post.cover_image_path)
                        rebuilt[i]["page"] = p.get("page", rebuilt[i]["page"])
                    elif ptype == "combined_summary_card":
                        rebuilt[i] = _combined_summary_page(snap_entity, revised_buy_rec)
                        rebuilt[i]["page"] = p.get("page", rebuilt[i]["page"])
                    elif ptype == "recommendation":
                        rebuilt[i] = _recommendation_page(revised_buy_rec)
                        rebuilt[i]["page"] = p.get("page", rebuilt[i]["page"])

                subjective_pages = [p for p in rebuilt if p.get("type") in _SUBJECTIVE_PAGE_TYPES]
                if subjective_pages:
                    import tempfile
                    from pathlib import Path
                    out_dir = Path(tempfile.mkdtemp(prefix="xhs_viz_"))
                    render_all_pages(
                        entity=snap_entity,
                        buy_rec=revised_buy_rec,
                        pages=subjective_pages,
                        title=revised.title,
                        out_dir=out_dir,
                    )
                new_pages = rebuilt

                post.buy_rating = revised_buy_rec.rating
                post.suitable_for = revised_buy_rec.suitable_for
                post.not_suitable_for = revised_buy_rec.not_suitable_for
                post.key_risks = revised_buy_rec.key_risks
                post.wait_for = revised_buy_rec.wait_for
                post.cover_text = revised_buy_rec.one_sentence
            except Exception as exc:
                log.warning("rewrite_image_regen_failed", post_id=post_id, error=str(exc))
                new_pages = None

        # Record iteration
        iterations = list(post.review_iterations or [])
        iteration_idx = len(iterations)
        iterations.append(
            {
                "feedback": feedback,
                "revised_title": revised.title,
                "revised_content": revised.content,
                "revised_hashtags": revised.hashtags,
                "revised_at": datetime.utcnow().isoformat(),
            }
        )
        post.review_iterations = iterations
        post.title = revised.title
        post.content = revised.content
        post.hashtags = revised.hashtags
        post.state = PostState.ITERATING
        post.llm_cost_usd = (post.llm_cost_usd or 0) + total_cost
        if new_pages is not None:
            post.pages = new_pages

        return revised, iteration_idx, new_pages


def _load_post_lite(post_id: int) -> Post:
    """Re-load a Post just for rendering (read-only). Caller must not mutate."""
    with session_scope() as s:
        post = PostRepository(s).get(post_id)
        if post is None:
            raise LookupError(f"Post {post_id} missing")
        # Detach so we can use after the session closes
        s.expunge(post)
        return post


# ============================================================
# Custom cover image upload
# ============================================================

# Permanent storage for user-uploaded cover images — survives the nightly
# tempfile cleanup and persists across multiple rewrite iterations (mirrors
# the assets/steam_assets/ caching convention).
_USER_COVERS_DIR = Path(__file__).resolve().parents[3] / "assets" / "user_covers"


async def _on_photo_upload(update, context) -> None:
    """Receive a user-uploaded image and use it as the candidate's cover.

    Replaces the auto-generated Steam-background cover with the user's own
    artwork verbatim (no title/rating/verdict text overlay). The choice is
    persisted on the post so it survives later rewrite iterations too.
    """
    if not _is_authorized(update):
        return

    post_id = _find_target_post(update)
    if post_id is None:
        await update.message.reply_text(
            "现在没有正在审的候选，不知道这张图是给哪条用的。先回复某条候选消息再发图～"
        )
        return

    photo = update.message.photo[-1] if update.message.photo else None
    if photo is None:
        return

    await update.message.reply_text(f"收到封面图，正在为候选 #{post_id} 替换…")

    try:
        tg_file = await photo.get_file()
        dest_dir = _USER_COVERS_DIR / str(post_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        dest_path = dest_dir / f"cover_{ts}.jpg"
        await tg_file.download_to_drive(str(dest_path))
    except Exception as exc:
        log.error("cover_upload_download_failed", post_id=post_id, error=str(exc))
        await update.message.reply_text(f"❌ 图片下载失败：{exc}")
        return

    try:
        new_cover_path = await asyncio.to_thread(
            _regen_cover_only_sync, post_id, str(dest_path)
        )
    except Exception as exc:
        log.error("cover_regen_failed", post_id=post_id, error=str(exc))
        await update.message.reply_text(f"❌ 封面替换失败：{exc}")
        return

    if new_cover_path is None:
        await update.message.reply_text("⚠️ 封面替换失败，候选保持原样。")
        return

    try:
        with open(new_cover_path, "rb") as fh:
            await update.message.reply_photo(
                photo=fh,
                caption=f"✅ 候选 #{post_id} 封面已替换为你上传的图片（后续修改意见也会保留这张封面）",
            )
    except Exception as exc:
        log.warning("cover_confirm_send_failed", post_id=post_id, error=str(exc))
        await update.message.reply_text(f"✅ 候选 #{post_id} 封面已替换为你上传的图片")


def _regen_cover_only_sync(post_id: int, cover_image_path: str) -> Optional[str]:
    """Rebuild + re-render only the cover page using the user's uploaded image.

    Lightweight path — no LLM calls, no full rewrite. Mirrors the
    snapshot/SimpleNamespace stand-in pattern used by `_rewrite_post_sync`,
    but only touches the cover page; everything else (objective charts,
    other subjective cards) is left untouched.

    Returns the path to the newly-rendered cover image, or None on failure.
    """
    import tempfile

    from types import SimpleNamespace

    from xhs_agent.agents.buy_or_wait import BuyRecommendation
    from xhs_agent.processors.page_builder import _cover_page
    from xhs_agent.visualization import render_all_pages

    with session_scope() as s:
        post = PostRepository(s).get(post_id)
        if post is None or not post.pages:
            return None

        snapshot = post.objective_snapshot or {}
        snap_entity = SimpleNamespace(**{
            "appid": snapshot.get("appid"),
            "name": snapshot.get("name") or post.trigger_entity_name,
            "historical_positive_rate": snapshot.get("historical_positive_rate"),
            "recent_7d_positive_rate": snapshot.get("recent_7d_positive_rate"),
            "recent_7d_review_count": snapshot.get("recent_7d_review_count"),
            "total_reviews": snapshot.get("total_reviews"),
            "current_player_count": snapshot.get("current_player_count"),
        })
        buy_rec = BuyRecommendation(
            rating=post.buy_rating or "C",
            one_sentence=post.cover_text or "",
            suitable_for=list(post.suitable_for or []),
            not_suitable_for=list(post.not_suitable_for or []),
            key_risks=list(post.key_risks or []),
            wait_for=post.wait_for or "",
        )

        pages = list(post.pages or [])
        cover_page = None
        for i, p in enumerate(pages):
            if p.get("type") == "cover":
                rebuilt = _cover_page(snap_entity, buy_rec, post.title, post.content,
                                      cover_image_path=cover_image_path)
                rebuilt["page"] = p.get("page", rebuilt["page"])
                pages[i] = rebuilt
                cover_page = rebuilt
                break

        if cover_page is None:
            return None

        out_dir = Path(tempfile.mkdtemp(prefix="xhs_viz_"))
        render_all_pages(
            entity=snap_entity,
            buy_rec=buy_rec,
            pages=[cover_page],
            title=post.title,
            out_dir=out_dir,
        )

        post.pages = pages
        post.cover_image_path = cover_image_path

        return cover_page.get("image_path")
