"""映章 Streamlit UI. Run through the project's root app.py; never starts a worker."""

from __future__ import annotations

import html
import json
import logging
import math
import os
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import streamlit as st

from novel_anime.application.service import StudioService
from novel_anime.core.errors import AppError
from novel_anime.domain.models import ModelProfile, Shot, VisualProfile
from novel_anime.ui.export import export_json, export_markdown, safe_payload

LOGGER = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 2_000_000
PAGES = ["章节原文", "改编大纲", "分镜工作台", "本章分析与角色资料", "文本任务"]
STATUS = {
    "pending": "等待执行",
    "queued": "等待执行",
    "running": "执行中",
    "succeeded": "已完成",
    "completed": "已完成",
    "failed": "失败",
    "paused": "已暂停",
    "pause_requested": "已请求暂停 · 等待阶段边界",
    "review_required": "文本已完成 · 待复核",
    "unknown": "执行状态待核对",
    "cancelled": "已取消",
    "submitting": "正在提交",
    "cancel_requested": "取消请求待确认",
}


def secret_values(profiles: dict[str, Any]) -> tuple[str, ...]:
    """Read configured credentials only for redaction; never display their values."""
    return tuple(
        value
        for profile in profiles.values()
        if (name := profile.get("api_key_env")) and (value := os.environ.get(name))
    )


def report_error(error: Exception, secrets: tuple[str, ...] = ()) -> None:
    """Show a stable domain error without displaying tracebacks or provider payloads."""
    if isinstance(error, AppError):
        message = str(getattr(error, "message", "操作未完成，请检查输入并重试。"))
        code = str(getattr(error, "code", "application_error"))
        st.error(f"{safe_payload(message, secrets)} · {code}")
    elif isinstance(error, (ValueError, json.JSONDecodeError)):
        st.error("输入格式或字段校验未通过，请检查必填项、JSON 类型和字段约束。")
    else:
        incident = uuid.uuid4().hex[:10]
        LOGGER.error("UI operation failed incident=%s type=%s", incident, type(error).__name__)
        st.error(f"操作未完成。请查看本地诊断日志或联系维护者。关联 ID：{incident}")


def execute(action: Callable[[], Any], message: str = "已保存") -> Any:
    try:
        value = action()
        st.session_state["notice"] = message
        st.rerun()
        return value
    except Exception as error:
        report_error(error, st.session_state.get("redaction_secrets", ()))
        return None


def render_value(value: Any) -> None:
    if isinstance(value, str):
        st.write(safe_payload(value, st.session_state.get("redaction_secrets", ())))
    elif value:
        st.json(safe_payload(value, st.session_state.get("redaction_secrets", ())), expanded=True)
    else:
        st.caption("此项尚无结果。")


def local_time(value: Any) -> str:
    if not value:
        return "尚无更新时间"
    try:
        return (
            datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            .astimezone()
            .strftime("%m-%d %H:%M:%S %Z")
        )
    except ValueError:
        return str(value)


def sidebar(service: StudioService) -> tuple[str | None, str | None, str]:
    with st.sidebar:
        st.markdown(
            '<div class="brand"><span>映</span><div><b>映章</b>'
            "<small>STORY TO FRAME</small></div></div>",
            unsafe_allow_html=True,
        )
        works = service.list_works()
        work_id = (
            st.selectbox(
                "当前作品",
                [w["id"] for w in works],
                format_func=lambda key: next(w["title"] for w in works if w["id"] == key),
                key="work_choice",
                placeholder="先创建一部作品",
            )
            if works
            else None
        )
        with st.expander("＋ 新建作品", expanded=not works):
            with st.form("new_work"):
                title = st.text_input("作品名称", max_chars=200)
                if st.form_submit_button("创建作品", use_container_width=True):
                    if title.strip():
                        execute(lambda: service.create_work(title.strip()), "作品已创建")
                    else:
                        st.error("请填写作品名称。")
        chapters = service.list_chapters(work_id) if work_id else []
        chapter_id = (
            st.selectbox(
                "当前章节",
                [None] + [chapter["id"] for chapter in chapters],
                format_func=lambda key: (
                    "＋ 新章节"
                    if key is None
                    else next(chapter["title"] for chapter in chapters if chapter["id"] == key)
                ),
                key=f"chapter_choice_{work_id}",
            )
            if work_id
            else None
        )
        st.markdown('<div class="nav-caption">创 作 空 间</div>', unsafe_allow_html=True)
        if st.session_state.get("page") not in PAGES:
            st.session_state["page"] = PAGES[0]
        page = st.radio(
            "页面导航", PAGES, label_visibility="collapsed", key="page",
            on_change=lambda: st.session_state.update(settings_open=False),
        )
        st.divider()
        st.markdown('<div class="nav-caption">设 置</div>', unsafe_allow_html=True)
        st.button(
            "模型与工作流", use_container_width=True,
            type="primary" if st.session_state.get("settings_open") else "secondary",
            on_click=lambda: st.session_state.update(settings_open=True),
        )
        if st.session_state.get("settings_open"):
            page = "模型与工作流"
            st.button(
                "返回创作空间", use_container_width=True,
                on_click=lambda: st.session_state.update(settings_open=False),
            )
        st.divider()
        st.caption("本地创作 · 文本改编首期")
        st.caption("原文 → 本章分析 → 动漫改编 → 分镜审阅")
        st.caption("视频生成暂未开放。文本任务由独立执行进程处理。")
    return work_id, chapter_id, page


def header(page: str, chapter: dict[str, Any] | None, run: dict[str, Any] | None) -> None:
    title = html.escape(chapter["title"] if chapter else "让文字，成为画面。")
    st.markdown(
        '<div class="eyebrow">YINGZHANG / LOCAL CREATIVE STUDIO</div>', unsafe_allow_html=True
    )
    st.title("设置" if page == "模型与工作流" else page)
    if page == "模型与工作流":
        st.caption("模型与工作流 · 全局配置")
        return
    st.caption(title if not chapter else f"{chapter['title']} · 原文 v{chapter['revision']}")
    stages = ["原文理解", "本章分析", "动漫改编", "分镜与提示词", "视频 · 未开放"]
    st.markdown(
        '<div class="pipeline">'
        + "".join(
            f"<div><span>{index:02d}</span>{label}</div>" for index, label in enumerate(stages, 1)
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    if run:
        st.caption(
            f"当前运行 {run['id']} · {STATUS.get(run['status'], run['status'])}"
            f" · 阶段 {run.get('stage') or '尚未开始'} · 结果 v{run.get('revision', 0)}"
        )


def source_page(
    service: StudioService, work_id: str | None, chapter: dict[str, Any] | None
) -> None:
    if not work_id:
        st.info("从左侧创建作品，然后保存你的第一章。这里没有预装示例数据。")
        return
    st.write("只提交本章正文与主动填写的前情。保存草稿后，再创建文本改编任务。")
    key = f"source_{chapter['id'] if chapter else 'new'}_{work_id}"
    upload = st.file_uploader("导入 UTF-8 TXT · 最大 2 MB", type=["txt"], key=f"upload_{key}")
    if upload is not None and st.button("将 TXT 载入正文编辑器", key=f"load_{key}"):
        if upload.size > MAX_UPLOAD_BYTES:
            st.error("文件超过 2 MB，请拆分章节后导入。")
        else:
            try:
                text = upload.getvalue().decode("utf-8-sig")
                if "\x00" in text:
                    raise ValueError("binary input")
                st.session_state[f"text_{key}"] = text
                st.success("正文已载入编辑器，尚未保存。")
            except (UnicodeDecodeError, ValueError):
                st.error("文件必须是有效 UTF-8 文本，且不包含二进制空字节。")
    with st.form(f"form_{key}"):
        title = st.text_input(
            "章节标题", value=chapter["title"] if chapter else "", max_chars=200, key=f"title_{key}"
        )
        text = st.text_area(
            "章节正文",
            value=chapter["text"] if chapter else "",
            height=400,
            key=f"text_{key}",
            placeholder="把这一章的故事放在这里……",
        )
        context = st.text_area(
            "可选前情",
            value=chapter.get("context", "") if chapter else "",
            height=110,
            key=f"context_{key}",
            help="仅填写理解本章必需的背景，不自动读取其他章节。",
        )
        st.caption("保存到本地数据库。刷新可恢复已保存草稿；提交任务采用保存时的输入版本。")
        submitted = st.form_submit_button("保存持久草稿", type="primary")
        if submitted:
            if not title.strip() or not text.strip():
                st.error("章节标题和正文不能为空。")
            elif len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
                st.error("正文超过 2 MB，请拆分章节。")
            else:

                def save() -> None:
                    saved_id = service.save_chapter(
                        work_id,
                        title.strip(),
                        text,
                        context,
                        chapter_id=chapter["id"] if chapter else None,
                        expected_revision=chapter["revision"] if chapter else None,
                    )
                    # Selection happens on the next render, before the selectbox is created.
                    st.session_state["select_saved_chapter"] = (work_id, saved_id)

                execute(save, "原文草稿已保存；历史运行继续保留采用时的输入。")
    if chapter:
        with st.expander("保存版本与输入范围"):
            st.write(f"章节 ID：{chapter['id']} · 当前版本 {chapter['revision']}")
            st.caption("修改原文后请创建新的文本运行，旧运行不会自动变成新原文的结果。")


def task_submit(
    service: StudioService, chapter: dict[str, Any] | None, profiles: dict[str, dict[str, Any]]
) -> None:
    with st.expander("＋ 创建文本改编任务", expanded=True):
        if not chapter:
            st.info("请先在「章节原文」保存章节。")
            return
        if not profiles:
            st.info("请先在「模型与工作流」保存模型配置。")
            return
        with st.form(f"submit_{chapter['id']}"):
            profile_id = st.selectbox(
                "采用模型配置",
                list(profiles),
                format_func=lambda key: f"{key} · {profiles[key].get('model', '未配置模型')}",
            )
            cols = st.columns(2)
            style = cols[0].selectbox("视觉风格", ["二维动画", "日系动画", "国风动画", "写实动画"])
            ratio = cols[1].selectbox("画幅", ["16:9", "9:16", "1:1", "4:3"])
            preference = st.text_area(
                "改编偏好（可选）",
                height=85,
                placeholder="例如：保留悬念，将内心独白转化为可见动作。",
            )
            st.caption(
                f"将提交已保存的原文 v{chapter['revision']}，并保存模型、风格与角色资料快照。"
            )
            submitted = st.form_submit_button("提交文本任务", type="primary")
            if submitted:
                token_key = f"submit_token_{chapter['id']}"
                token = st.session_state.setdefault(token_key, uuid.uuid4().hex)

                def submit() -> None:
                    service.create_run(
                        chapter["id"], profiles[profile_id], style, ratio, preference, token
                    )
                    st.session_state[token_key] = uuid.uuid4().hex

                execute(submit, "文本任务已入队。需要独立 worker 在线执行，界面不会启动执行器。")


@st.fragment(run_every="3s")
def task_monitor(service: StudioService, chapter_id: str) -> None:
    runs = service.list_runs(chapter_id)
    st.subheader("文本运行记录")
    st.caption("每 3 秒读取持久化状态。完成仅表示文本阶段完成，视频仍未提交。")
    if not runs:
        st.info("暂无文本任务。保存配置后提交第一条任务。")
        return
    summary = st.columns(3)
    summary[0].metric("文本运行", len(runs))
    summary[1].metric(
        "已完成文本", sum(item["status"] in {"succeeded", "review_required"} for item in runs)
    )
    summary[2].metric("失败运行", sum(item["status"] == "failed" for item in runs))
    for run in runs:
        with st.container(border=True):
            a, b = st.columns([3, 1])
            a.markdown(
                f"**{STATUS.get(run['status'], run['status'])}** · {run.get('stage') or '等待领取'}"
            )
            b.caption(local_time(run.get("updated_at")))
            st.caption(f"运行 {run['id']} · 结果版本 {run.get('revision', 0)}")
            if run.get("error_message"):
                st.error(
                    safe_payload(
                        run["error_message"], st.session_state.get("redaction_secrets", ())
                    )
                )
            if run.get("validation_issues"):
                with st.expander("校验问题"):
                    render_value(run["validation_issues"])
            if run["status"] == "unknown":
                st.warning(
                    "执行结果尚无法确认。请先核对供应商请求记录，再手动重试；重试可能重复计费。"
                )
            controls = [
                ("暂停", "pause_run", run["status"] in {"pending", "queued", "running"}),
                ("继续", "resume_run", run["status"] == "paused"),
                ("重试", "retry_run", run["status"] in {"failed", "unknown"}),
            ]
            for col, (label, method, enabled) in zip(st.columns(3), controls):
                if col.button(label, disabled=not enabled, key=f"{label}_{run['id']}"):
                    execute(
                        partial(getattr(service, method), run["id"]),
                        {
                            "暂停": "已请求暂停，将在阶段边界生效。",
                            "继续": "已请求继续未完成阶段。",
                            "重试": "已请求重试最后未完成阶段，已完成阶段保留。",
                        }[label],
                    )
    st.caption("暂停在阶段边界生效；继续与重试由独立执行进程处理，不会重新执行已成功阶段。")


def outline_page(run: dict[str, Any] | None) -> None:
    run = run or {}
    outline = (run.get("result") or {}).get("outline")
    if not outline:
        st.info("暂无改编大纲。请先保存章节并提交文本任务，执行完成后在此审阅。")
        return
    st.subheader("故事梗概")
    render_value(outline.get("synopsis"))
    a, b = st.columns([1, 2])
    with a:
        st.subheader("改编取舍")
        render_value(outline.get("changes"))
    with b:
        st.subheader("场景规划")
        render_value(outline.get("scenes"))
    with st.expander("采用时的输入与配置快照"):
        render_value(run.get("document", {}))


def shots_page(service: StudioService, run: dict[str, Any] | None) -> None:
    run = run or {}
    result = run.get("result") or {}
    shots = result.get("shots") or []
    if not shots:
        st.info("这里会成为你的分镜工作台。当前没有真实分镜结果，请先完成文本任务。")
        st.button("生成镜头视频 · 首期未开放", disabled=True)
        return
    cols = st.columns(3)
    cols[0].metric("本版镜头", len(shots))
    duration = sum(float(s.get("duration_seconds") or 0) for s in shots)
    cols[1].metric("规划素材时长", f"{duration:g} 秒" if duration else "尚未标注")
    cols[2].metric("视频状态", "未提交")
    st.caption("真实视频耗时尚未实测；规划时长不等于已有视频。")
    scenes = list(dict.fromkeys(str(s.get("scene_id") or "未分场景") for s in shots))
    a, b = st.columns([3, 1])
    scene = a.selectbox("场景筛选", ["全部场景"] + scenes)
    page_size = b.selectbox("每页镜头", [6, 12, 24])
    visible = [
        (index, shot)
        for index, shot in enumerate(shots)
        if scene == "全部场景" or str(shot.get("scene_id") or "未分场景") == scene
    ]
    page_count = max(1, math.ceil(len(visible) / page_size))
    page = st.number_input(
        "页码",
        min_value=1,
        max_value=page_count,
        value=1,
        key=f"shots_page_{run['id']}_{scene}_{page_size}",
    )
    st.caption(f"筛选结果 {len(visible)} 镜 · 第 {page} / {page_count} 页")
    cards = st.columns(2)
    for position, (index, shot) in enumerate(visible[(page - 1) * page_size : page * page_size]):
        with cards[position % 2], st.container(border=True):
            st.caption(
                f"{shot.get('shot_id', shot.get('id', index + 1))} / "
                f"{shot.get('scene_id', '未分场景')}"
            )
            st.markdown(f"**{shot.get('title') or f'镜头 {index + 1:03d}'}**")
            st.write(shot.get("action") or shot.get("description") or "尚未填写画面动作")
            st.caption(
                f"{shot.get('shot_size', shot.get('size', '景别未标注'))} · "
                f"{shot.get('camera', '机位未标注')}"
            )
            st.caption(f"原文依据：{', '.join(shot.get('source_paragraph_ids') or []) or '未标注'}")
    st.divider()
    st.subheader("镜头细节 · 保存新版本")
    selected = st.selectbox(
        "选择镜头",
        list(range(len(shots))),
        format_func=lambda index: (
            f"{index + 1:03d} · {shots[index].get('title') or shots[index].get('shot_id', '')}"
        ),
    )
    shot = shots[selected]
    editor_key = f"shot_{run['id']}_{run['revision']}_{selected}"
    with st.form(editor_key):
        left, right = st.columns(2)
        title = left.text_input("镜头标题", shot.get("title", ""))
        size = right.text_input("景别", shot.get("shot_size", ""))
        purpose = left.text_area("叙事作用", shot.get("story_purpose", ""), height=100)
        action = right.text_area("画面动作", shot.get("action", ""), height=100)
        camera = left.text_input("机位与运镜", shot.get("camera", ""))
        duration = right.number_input(
            "规划时长 / 秒", min_value=0.1, value=float(shot.get("duration_seconds", 5))
        )
        start = left.text_area("起始状态", shot.get("start_state", ""), height=85)
        end = right.text_area("结束状态", shot.get("end_state", ""), height=85)
        prompt = st.text_area("视频提示词 · 语义草案", shot.get("video_prompt", ""), height=150)
        st.caption(
            "提示词仅供审阅与导出，当前不会发送到视频模型。原文引用、连续性和其他字段可在下方编辑。"
        )
        with st.expander("引用、连续性与其他字段"):
            editable = {
                key: value
                for key, value in shot.items()
                if key
                not in {
                    "title",
                    "shot_size",
                    "story_purpose",
                    "action",
                    "camera",
                    "duration_seconds",
                    "start_state",
                    "end_state",
                    "video_prompt",
                }
            }
            draft = st.text_area(
                "扩展镜头字段 JSON",
                value=json.dumps(editable, ensure_ascii=False, indent=2),
                height=260,
                help="保留稳定镜头 ID、场景与原文引用。保存时按 Shot 契约校验。",
            )
        st.caption("保存生成新的分镜版本，旧版本由服务保留。")
        if st.form_submit_button("保存镜头为新版本", type="primary"):
            try:
                edited = Shot.model_validate(
                    {
                        **json.loads(draft),
                        "title": title,
                        "shot_size": size,
                        "story_purpose": purpose,
                        "action": action,
                        "camera": camera,
                        "duration_seconds": duration,
                        "start_state": start,
                        "end_state": end,
                        "video_prompt": prompt,
                    }
                ).model_dump(mode="json")
                updated = [dict(item) for item in shots]
                updated[selected] = edited
                execute(
                    lambda: service.save_run_shots(run["id"], updated, run["revision"]),
                    "镜头新版本已保存。请复核角色资料与上下文连续性。",
                )
            except Exception as error:
                report_error(error)
    with st.expander("来源段落、审阅与连续性"):
        render_value(result.get("paragraphs"))
        render_value(result.get("review"))
    secrets = st.session_state.get("redaction_secrets", ())
    downloads = st.columns(3)
    downloads[0].download_button(
        "↓ 导出 JSON",
        export_json(run, secrets),
        file_name=f"yingzhang-{run['id']}-v{run['revision']}.json",
        mime="application/json",
    )
    downloads[1].download_button(
        "↓ 导出 Markdown",
        export_markdown(run, secrets),
        file_name=f"yingzhang-{run['id']}-v{run['revision']}.md",
        mime="text/markdown",
    )
    downloads[2].button("生成视频 · 未开放", disabled=True)
    st.caption("导出包含采用时的模型、风格、资料与来源快照；真实密钥字段与已知凭证值已脱敏。")


def analysis_page(service: StudioService, work_id: str | None, run: dict[str, Any] | None) -> None:
    tabs = st.tabs(["本章分析", "角色视觉资料", "运行来源与审阅"])
    with tabs[0]:
        analysis = ((run or {}).get("result") or {}).get("analysis")
        if not analysis:
            st.info("本章分析尚未生成。人物关系仅依据当前输入，不自动扩展为完整跨章记忆库。")
        else:
            for key, label in [
                ("summary", "本章概要"),
                ("core_conflict", "核心冲突"),
                ("characters", "本章人物"),
                ("events", "叙事事件"),
                ("relations", "人物关系"),
                ("uncertainties", "缺口与待核实"),
            ]:
                st.subheader(label)
                render_value(analysis.get(key))
    with tabs[1]:
        if not work_id:
            st.info("先创建作品，即可保存跨章节复用的角色视觉资料。")
        else:
            profiles = service.list_visual_profiles(work_id)
            st.caption("保存为新资料版本。既有运行保留采用时的快照，不自动重写已生成分镜。")
            for profile in profiles:
                with st.expander(
                    f"{profile.get('character_id', '角色')} · v{profile.get('version', '?')}"
                ):
                    render_value(profile)
            with st.expander("新增 / 编辑角色资料", expanded=not profiles):
                options = [None] + list(range(len(profiles)))
                selected = st.selectbox(
                    "选择资料",
                    options,
                    format_func=lambda index: (
                        "＋ 新建"
                        if index is None
                        else f"{profiles[index].get('character_id')} · "
                        f"v{profiles[index].get('version')}"
                    ),
                )
                original = profiles[selected] if selected is not None else {}
                with st.form(f"visual_{work_id}_{selected}"):
                    left, right = st.columns(2)
                    character_id = left.text_input(
                        "角色稳定 ID",
                        original.get("character_id", ""),
                        help="例如 CHAR_001；同一角色的新版沿用相同 ID。",
                    )
                    name = right.text_input("角色名称", original.get("name", ""))
                    appearance = left.text_area(
                        "基础外貌", original.get("appearance", ""), height=120
                    )
                    costume = right.text_area("服装描述", original.get("costume", ""), height=120)
                    costume_version = left.text_input(
                        "服装版本编号", original.get("costume_version", "costume_01")
                    )
                    origin = right.selectbox(
                        "资料依据",
                        ["改编设定", "用户提供", "正文明确描述"],
                        index=["改编设定", "用户提供", "正文明确描述"].index(
                            original.get("origin", "改编设定")
                        )
                        if original.get("origin", "改编设定")
                        in ["改编设定", "用户提供", "正文明确描述"]
                        else 0,
                    )
                    reference = st.text_input(
                        "参考素材备注",
                        original.get("reference", ""),
                        help="只保存备注，不自动上传或读取文件。",
                    )
                    if st.form_submit_button("保存为新资料版本", type="primary"):
                        try:
                            data = {
                                "character_id": character_id,
                                "name": name,
                                "appearance": appearance,
                                "costume": costume,
                                "costume_version": costume_version,
                                "origin": origin,
                                "reference": reference,
                                "version": int(original.get("version", 0)) + 1,
                            }
                            profile = VisualProfile.model_validate(data).model_dump(mode="json")
                            execute(
                                lambda: service.save_visual_profile(work_id, profile),
                                "角色视觉资料新版本已保存",
                            )
                        except Exception as error:
                            report_error(error)
                with st.expander("角色资料字段说明"):
                    st.json(VisualProfile.model_json_schema())
    with tabs[2]:
        if run:
            render_value((run.get("result") or {}).get("review"))
            render_value((run.get("result") or {}).get("provenance"))
            render_value(run.get("validation_issues"))
            with st.expander("采用时的输入与配置"):
                render_value(run.get("document"))
        else:
            st.info("暂无运行来源记录。")


def settings_page(service: StudioService, profiles: dict[str, dict[str, Any]]) -> None:
    st.write("线上 API 与 LM Studio 共用统一文本接口。保存配置不会自动发送任何请求。")
    options = list(profiles) + ["＋ 新建线上配置", "＋ 新建 LM Studio"]
    selected = st.selectbox("模型配置", options)
    local = selected == "＋ 新建 LM Studio"
    default = profiles.get(selected, {})
    with st.form(f"profile_{selected}"):
        a, b = st.columns(2)
        profile_id = a.text_input(
            "配置 ID", default.get("profile_id", "local" if local else "online")
        )
        provider = b.text_input("提供商", default.get("provider", "LM Studio" if local else ""))
        protocol = a.selectbox(
            "协议",
            ["chat_completions", "responses"],
            index=int(default.get("protocol") == "responses"),
        )
        base_url = b.text_input(
            "服务地址", default.get("base_url", "http://127.0.0.1:1234/v1" if local else "")
        )
        catalog_key = f"model_catalog_{selected}"
        catalog = st.session_state.get(catalog_key, {})
        model_options = catalog.get("models", [])
        if model_options:
            model = a.selectbox(
                "可用模型",
                model_options,
                index=model_options.index(default["model"])
                if default.get("model") in model_options
                else 0,
            )
            a.caption("模型列表由服务返回；选择后可发送小请求验证实际可调用性。")
        else:
            model = default.get("model", "")
            a.caption(f"当前模型：{model or '尚未选择，请先获取可用模型'}")
        env_name = b.text_input(
            "密钥环境变量名称（不是密钥）",
            default.get("api_key_env", "" if local else "NOVEL_LLM_API_KEY"),
            help="仅保存环境变量名。真实密钥由进程环境读取。",
        )
        context_budget = int(
            a.number_input(
                "可用上下文预算 / token",
                min_value=1,
                value=int(default.get("context_budget", 16000)),
            )
        )
        max_output = int(
            b.number_input(
                "最大输出 / token", min_value=1, value=int(default.get("max_output_tokens", 4000))
            )
        )
        timeout = float(
            a.number_input("请求超时 / 秒", min_value=1.0, value=float(default.get("timeout", 120)))
        )
        retries = int(
            b.number_input(
                "有限重试上限", min_value=0, max_value=5, value=int(default.get("retries", 2))
            )
        )
        structured = st.checkbox(
            "启用结构化输出（确认服务支持后开启）",
            value=bool(default.get("structured_output", False)),
        )
        fetch_models = st.form_submit_button("获取可用模型")
        if fetch_models:
            try:
                from novel_anime.integrations.language_models.client import LLMClient

                endpoint = urlsplit(base_url)
                if (
                    endpoint.scheme not in {"http", "https"}
                    or not endpoint.hostname
                    or endpoint.username
                    or endpoint.password
                    or endpoint.query
                    or endpoint.fragment
                ):
                    st.error("请填写不含凭证与查询参数的有效 HTTP(S) 服务地址。")
                else:
                    discovery = ModelProfile(
                        provider=provider or "catalog",
                        protocol="chat_completions",
                        base_url=base_url,
                        model="catalog",
                        api_key_env=env_name,
                        timeout=timeout,
                    )
                    with st.spinner("正在获取服务模型列表……"):
                        model_ids = LLMClient(discovery).list_models()
                    st.session_state[catalog_key] = {
                        "models": model_ids,
                        "base_url": base_url,
                        "api_key_env": env_name,
                    }
                    st.rerun()
            except Exception as error:
                report_error(error, secret_values(profiles))
        if st.form_submit_button("保存模型配置", type="primary"):
            try:
                if catalog and (
                    catalog.get("base_url") != base_url or catalog.get("api_key_env") != env_name
                ):
                    st.error("连接配置已更改，请重新获取可用模型后保存。")
                    return
                if not model:
                    st.error("请先获取可用模型并选择一个模型。")
                    return
                if env_name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", env_name):
                    raise ValueError("invalid environment name")
                endpoint = urlsplit(base_url)
                if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
                    st.error("服务地址必须是有效的 HTTP(S) 地址。")
                    return
                if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                    st.error("服务地址不可包含凭证、查询参数或片段；密钥只能通过环境变量读取。")
                    return
                profile = ModelProfile(
                    profile_id=profile_id,
                    provider=provider,
                    protocol=cast(Literal["chat_completions", "responses"], protocol),
                    base_url=base_url,
                    model=model,
                    api_key_env=env_name,
                    context_budget=context_budget,
                    max_output_tokens=max_output,
                    timeout=timeout,
                    retries=retries,
                    structured_output=structured,
                ).model_dump(mode="json")
                execute(lambda: service.save_profile(profile), "模型配置已保存；尚未测试实际请求。")
            except Exception as error:
                report_error(error)
    if default:
        name = default.get("api_key_env")
        if name:
            st.caption(
                f"环境变量 {name}：{'已设置' if os.environ.get(name) else '当前 UI 进程未设置'}。"
                "worker 需独立继承该环境变量。"
            )
        st.caption(
            "测试会发送一个真实小请求，可能计费；仅验证当前保存的协议、模型与基础输出，不代表完整改编质量。"
        )
        if st.button("测试已保存配置 · 实际小请求"):
            try:
                from novel_anime.integrations.language_models.client import LLMClient

                with st.spinner("正在请求已保存的模型配置……"):
                    response = LLMClient(ModelProfile(**default)).probe()
                st.success("实际小请求返回成功。")
                render_value(
                    response.model_dump(mode="json")
                    if hasattr(response, "model_dump")
                    else response
                )
            except Exception as error:
                report_error(error, secret_values(profiles))
    with st.expander("如何设置密钥并启动 · 不在页面粘贴真实 Key"):
        st.markdown(
            "**Windows PowerShell**：在启动 UI 与 worker 的同一个终端中设置；另开终端时需重新设置。"
        )
        st.code(
            '$env:NOVEL_LLM_API_KEY = "你的实际密钥"\npython start.py',
            language="powershell",
        )
        st.markdown("**macOS / Linux**：环境变量仅对当前终端及其子进程生效。")
        st.code(
            'export NOVEL_LLM_API_KEY="你的实际密钥"\npython start.py',
            language="bash",
        )
        st.caption(
            "请将 NOVEL_LLM_API_KEY 替换为上方配置的变量名。启动器同时启动界面与独立 worker，"
            "并继承当前终端环境。修改环境后重启启动器。LM Studio 无需认证时变量名可留空。"
        )
    st.divider()
    st.subheader("ComfyUI 视频工作流 · 保留配置")
    st.info("首期只执行文本改写。视频生成禁用，保存此配置不会连接 ComfyUI，也不会创建视频任务。")
    video = service.load_video_config() or {}
    with st.form("video_config"):
        url = st.text_input("ComfyUI 服务地址", video.get("base_url", "http://127.0.0.1:8188"))
        workflow = st.text_input("API 工作流模板路径 / 备注", video.get("workflow", ""))
        ratio = st.selectbox(
            "默认视频画幅",
            ["16:9", "9:16", "1:1"],
            index=["16:9", "9:16", "1:1"].index(video.get("aspect_ratio", "16:9"))
            if video.get("aspect_ratio", "16:9") in ["16:9", "9:16", "1:1"]
            else 0,
        )
        if st.form_submit_button("保存视频配置"):
            execute(
                lambda: service.save_video_config(
                    {**video, "base_url": url, "workflow": workflow, "aspect_ratio": ratio}
                ),
                "视频配置已保存，尚未验证工作流。",
            )
    st.button("提交视频生成 · 未开放", disabled=True)


def main() -> None:
    st.set_page_config(
        page_title="映章 · 小说动漫工作台",
        page_icon="🌿",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        f"<style>{Path(__file__).with_name('theme.css').read_text(encoding='utf-8')}</style>",
        unsafe_allow_html=True,
    )
    try:
        service = StudioService()
        if saved := st.session_state.pop("select_saved_chapter", None):
            st.session_state[f"chapter_choice_{saved[0]}"] = saved[1]
        profiles = service.load_profiles()
        st.session_state["redaction_secrets"] = secret_values(profiles)
        work_id, chapter_id, page = sidebar(service)
        chapter = service.get_chapter(chapter_id) if chapter_id else None
        runs = service.list_runs(chapter_id) if chapter_id else []
        run = None
        if runs and page in ["改编大纲", "分镜工作台", "本章分析与角色资料"]:
            run_id = st.selectbox(
                "查看运行版本",
                [item["id"] for item in runs],
                format_func=lambda key: next(
                    f"{STATUS.get(item['status'], item['status'])} · "
                    f"{local_time(item.get('updated_at'))} · {key}"
                    for item in runs
                    if item["id"] == key
                ),
                key=f"run_{chapter_id}",
            )
            run = next(item for item in runs if item["id"] == run_id)
        elif runs:
            run = runs[0]
        header(page, chapter, run)
        if notice := st.session_state.pop("notice", None):
            st.success(notice)
        if page == "章节原文":
            source_page(service, work_id, chapter)
        elif page == "改编大纲":
            outline_page(run)
        elif page == "分镜工作台":
            shots_page(service, run)
        elif page == "本章分析与角色资料":
            analysis_page(service, work_id, run)
        elif page == "文本任务":
            task_submit(service, chapter, profiles)
            if chapter_id:
                task_monitor(service, chapter_id)
        else:
            settings_page(service, profiles)
    except Exception as error:
        report_error(error, st.session_state.get("redaction_secrets", ()))


if __name__ == "__main__":
    main()
