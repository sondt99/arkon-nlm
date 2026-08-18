"""
Arkon Documentation Exporter (legacy).

The canonical docs for v0.1.0 are the Markdown files in this folder
and the root README.md — start at docs/README.md.

This script can still emit docs/Arkon-Documentation.docx. It is not
kept in lockstep with the Markdown rewrite. Prefer the .md files.

Run: python docs/export_docs.py
"""

import io
import re
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
import matplotlib.patheffects as pe

from docx import Document
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ── Paths ────────────────────────────────────────────────────────────────────
DOCS_DIR = Path(__file__).parent
OUTPUT   = DOCS_DIR / "Arkon-Documentation.docx"

# Vietnamese-capable font on Windows
VN_FONT = "Arial"

# ── Color Palette ─────────────────────────────────────────────────────────────
C = {
    "primary":   "#1565C0",
    "secondary": "#0288D1",
    "accent":    "#00ACC1",
    "success":   "#2E7D32",
    "warning":   "#F57F17",
    "danger":    "#C62828",
    "purple":    "#6A1B9A",
    "gray":      "#546E7A",
    "light":     "#ECEFF1",
    "bg":        "#F5F5F5",
    "white":     "#FFFFFF",
}

def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16)/255 for i in (0, 2, 4))

# ── Diagram helpers ──────────────────────────────────────────────────────────

def box(ax, x, y, w, h, label, sublabel="", color="#1565C0", fontsize=10, text_color="white", radius=0.3):
    rect = FancyBboxPatch((x - w/2, y - h/2), w, h,
                          boxstyle=f"round,pad=0.05,rounding_size={radius}",
                          facecolor=hex_to_rgb(color), edgecolor="white",
                          linewidth=1.5, zorder=3)
    ax.add_patch(rect)
    ax.text(x, y + (0.08 if sublabel else 0), label,
            ha="center", va="center", fontsize=fontsize, fontweight="bold",
            color=text_color, zorder=4, fontfamily=VN_FONT)
    if sublabel:
        ax.text(x, y - 0.22, sublabel, ha="center", va="center",
                fontsize=fontsize - 2, color=text_color, alpha=0.85,
                zorder=4, fontfamily=VN_FONT)

def arrow(ax, x1, y1, x2, y2, label="", color="#546E7A", lw=1.5):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>", color=hex_to_rgb(color),
                                lw=lw, mutation_scale=14),
                zorder=2)
    if label:
        mx, my = (x1+x2)/2, (y1+y2)/2
        ax.text(mx+0.05, my, label, fontsize=7, color=hex_to_rgb(color),
                ha="left", va="center", fontfamily=VN_FONT,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))

def fig_to_bytes(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)
    return buf

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 1 — System Architecture
# ═══════════════════════════════════════════════════════════════════════════════
def make_architecture_diagram():
    fig, ax = plt.subplots(figsize=(14, 9))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 14); ax.set_ylim(0, 9)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("Kiến trúc hệ thống Arkon", fontsize=16, fontweight="bold",
                 color=hex_to_rgb(C["primary"]), pad=15, fontfamily=VN_FONT)

    # ── External users ──────────────────────────────
    box(ax, 2, 8.2, 2.2, 0.6, "Browser / Claude Desktop", "",
        C["gray"], 8, "white", 0.2)
    arrow(ax, 2, 7.9, 2, 7.35, "HTTPS")

    # ── Frontend ────────────────────────────────────
    box(ax, 2, 7.0, 2.5, 0.65, "Frontend", "Next.js 15 :3119",
        C["secondary"], 9)
    arrow(ax, 3.25, 7.0, 4.75, 7.0, "API calls")

    # ── API ─────────────────────────────────────────
    box(ax, 6, 7.0, 2.5, 0.65, "API Server", "FastAPI :5055",
        C["primary"], 9)

    # ── MCP ─────────────────────────────────────────
    box(ax, 11.5, 7.0, 2.4, 0.65, "MCP Server", "FastMCP /mcp",
        C["purple"], 9)
    arrow(ax, 7.25, 7.0, 10.3, 7.0, "mounted at /mcp")

    # ── Workers ─────────────────────────────────────
    box(ax, 2.5, 5.5, 2.6, 0.65, "Worker", "arq :ingest",
        C["accent"], 9)
    box(ax, 5.8, 5.5, 2.8, 0.65, "Worker Skills", "arq :skills",
        C["accent"], 9)
    arrow(ax, 6, 6.67, 6, 5.83, "enqueue jobs")
    arrow(ax, 6, 6.67, 2.5, 5.83, "enqueue jobs")

    # ── Databases row ────────────────────────────────
    box(ax, 1.5, 3.8, 2.0, 0.65, "PostgreSQL", "+pgvector :5432",
        C["success"], 9)
    box(ax, 4.0, 3.8, 1.8, 0.65, "Redis", ":6379",
        C["danger"], 9)
    box(ax, 6.5, 3.8, 1.8, 0.65, "MinIO", "S3-compat :9002",
        C["warning"], 9)

    # API ↔ DB
    arrow(ax, 6, 6.67, 1.5, 4.13, "SQL/ORM")
    arrow(ax, 6, 6.67, 4.0, 4.13, "queue")
    arrow(ax, 6, 6.67, 6.5, 4.13, "files")

    # Workers ↔ DB
    arrow(ax, 2.5, 5.17, 1.5, 4.13)
    arrow(ax, 2.5, 5.17, 4.0, 4.13)
    arrow(ax, 2.5, 5.17, 6.5, 4.13)
    arrow(ax, 5.8, 5.17, 1.5, 4.13)

    # ── MRP Pipeline box ────────────────────────────
    mrp_rect = FancyBboxPatch((8.5, 4.5), 5.0, 2.8,
                               boxstyle="round,pad=0.1",
                               facecolor=hex_to_rgb("#E8EAF6"),
                               edgecolor=hex_to_rgb(C["primary"]),
                               linewidth=1.5, linestyle="--")
    ax.add_patch(mrp_rect)
    ax.text(11.0, 7.4, "MRP Pipeline (Worker)", ha="center",
            fontsize=9, fontweight="bold", color=hex_to_rgb(C["primary"]),
            fontfamily=VN_FONT)

    phases = ["Triage", "MAP", "REDUCE", "REFINE", "VERIFY", "COMMIT"]
    colors_p = [C["accent"], C["secondary"], C["primary"],
                C["purple"], C["warning"], C["success"]]
    for i, (ph, cp) in enumerate(zip(phases, colors_p)):
        px = 9.0 + (i % 3) * 1.65
        py = 6.9 - (i // 3) * 1.1
        box(ax, px, py, 1.4, 0.55, ph, "", cp, 8, "white", 0.25)
        if i < 5:
            nx = 9.0 + ((i+1) % 3) * 1.65
            ny = 6.9 - ((i+1) // 3) * 1.1
            if i == 2:
                arrow(ax, px - 0.7, py - 0.28, nx - 0.7, ny + 0.28)
            elif i < 2:
                arrow(ax, px + 0.7, py, nx - 0.7, ny)
            else:
                arrow(ax, px + 0.7, py, nx - 0.7, ny)

    arrow(ax, 2.5, 5.17, 8.5, 5.9, "run pipeline")

    # ── External AI providers ────────────────────────
    box(ax, 11.0, 3.5, 2.8, 0.6, "AI Providers", "Google/OpenAI/Anthropic/Ollama",
        C["gray"], 7.5, "white", 0.2)
    arrow(ax, 11.0, 4.5, 11.0, 3.8, "LLM calls")

    # ── Legend ──────────────────────────────────────
    legend_items = [
        (C["secondary"], "Frontend"),
        (C["primary"],   "Backend API"),
        (C["accent"],    "Workers / arq"),
        (C["success"],   "PostgreSQL"),
        (C["danger"],    "Redis"),
        (C["warning"],   "MinIO"),
        (C["purple"],    "MCP / LLM"),
    ]
    for i, (lc, lt) in enumerate(legend_items):
        lx = 8.6 + (i % 4) * 1.35
        ly = 0.9 - (i // 4) * 0.45
        r = mpatches.Rectangle((lx, ly), 0.18, 0.18,
                                facecolor=hex_to_rgb(lc), edgecolor="none")
        ax.add_patch(r)
        ax.text(lx + 0.25, ly + 0.09, lt, fontsize=7.5,
                va="center", fontfamily=VN_FONT, color=hex_to_rgb(C["gray"]))

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 2 — MRP Pipeline
# ═══════════════════════════════════════════════════════════════════════════════
def make_mrp_pipeline():
    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 14); ax.set_ylim(0, 7)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("MRP Pipeline — Quy trình xử lý tài liệu", fontsize=15,
                 fontweight="bold", color=hex_to_rgb(C["primary"]),
                 pad=12, fontfamily=VN_FONT)

    phases = [
        ("1. TRIAGE",  C["accent"],    1.2,  "Phân loại → outline\nchọn pipeline strategy"),
        ("2. MAP",     C["secondary"], 3.3,  "Chia chunks → LLM\nextract entities / facts"),
        ("3. REDUCE",  C["primary"],   5.4,  "Gộp → wiki page drafts\ntheo slug"),
        ("4. REFINE",  C["purple"],    7.5,  "Cải thiện prose\nloại bỏ trùng lặp"),
        ("5. VERIFY",  C["warning"],   9.6,  "Kiểm tra fact\ncross-reference"),
        ("6. COMMIT",  C["success"],  11.7,  "Lưu wiki pages\nvào PostgreSQL"),
    ]

    for name, color, cx, desc in phases:
        box(ax, cx, 4.0, 1.85, 0.8, name, "", color, 10)
        ax.text(cx, 2.95, desc, ha="center", va="top", fontsize=8,
                color=hex_to_rgb(C["gray"]), fontfamily=VN_FONT,
                multialignment="center")
        ax.annotate("", xy=(cx, 3.6), xytext=(cx, 3.0),
                    arrowprops=dict(arrowstyle="-|>",
                                   color=hex_to_rgb(color), lw=1))

    # arrows between phases
    for i in range(len(phases) - 1):
        x1 = phases[i][2] + 0.93
        x2 = phases[i+1][2] - 0.93
        arrow(ax, x1, 4.0, x2, 4.0, color=C["gray"], lw=2)

    # Input
    box(ax, 1.2, 5.7, 1.85, 0.65, "Source\n(PDF/DOCX)", "",
        C["gray"], 9, "white", 0.2)
    arrow(ax, 1.2, 5.38, 1.2, 4.4, color=C["gray"])

    # Output
    box(ax, 11.7, 5.7, 1.85, 0.65, "Wiki Pages\n(PostgreSQL)", "",
        C["success"], 9, "white", 0.2)
    arrow(ax, 11.7, 4.4, 11.7, 5.38, color=C["success"])

    # DB access indicators
    ax.text(7.0, 1.3,
            "Mỗi phase đọc/ghi PostgreSQL • Redis queue • MinIO (files)",
            ha="center", fontsize=9, color=hex_to_rgb(C["gray"]),
            fontfamily=VN_FONT, style="italic",
            bbox=dict(boxstyle="round,pad=0.4", fc=hex_to_rgb(C["light"]),
                      ec=hex_to_rgb(C["secondary"]), lw=1))

    # plan_review gate
    box(ax, 3.3, 5.7, 1.85, 0.65, "Plan Review\n(optional gate)", "",
        C["warning"], 8.5, "white", 0.2)
    ax.annotate("", xy=(3.3, 5.38), xytext=(3.3, 4.4),
                arrowprops=dict(arrowstyle="<->", color=hex_to_rgb(C["warning"]),
                                lw=1.5, linestyle="dashed"))
    ax.text(3.9, 5.05, "approve?", fontsize=7.5,
            color=hex_to_rgb(C["warning"]), fontfamily=VN_FONT)

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 3 — Document Upload Flow
# ═══════════════════════════════════════════════════════════════════════════════
def make_upload_flow():
    fig, ax = plt.subplots(figsize=(13, 6.5))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 13); ax.set_ylim(0, 6.5)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("Luồng Upload & Xử lý Tài liệu", fontsize=15,
                 fontweight="bold", color=hex_to_rgb(C["primary"]),
                 pad=12, fontfamily=VN_FONT)

    steps = [
        (1.4, 5.0, "User\nUpload", C["gray"]),
        (3.5, 5.0, "POST\n/api/sources/upload", C["secondary"]),
        (6.0, 5.0, "MinIO\n(lưu file)", C["warning"]),
        (8.5, 5.0, "Redis Queue\n(enqueue job)", C["danger"]),
        (11.2, 5.0, "Worker\n(arq)", C["accent"]),
    ]
    for x, y, label, color in steps:
        box(ax, x, y, 1.9, 0.75, label, "", color, 9)

    for i in range(len(steps) - 1):
        arrow(ax, steps[i][0] + 0.95, steps[i][1],
              steps[i+1][0] - 0.95, steps[i+1][1], color=C["gray"], lw=2)

    # Worker → pipeline phases
    pipeline_y = 3.2
    p_items = [
        (2.0, "Triage", C["accent"]),
        (4.2, "MAP", C["secondary"]),
        (6.4, "REDUCE", C["primary"]),
        (8.6, "REFINE", C["purple"]),
        (10.8, "VERIFY", C["warning"]),
    ]
    arrow(ax, 11.2, 4.625, 10.8, pipeline_y + 0.35, color=C["gray"])
    for i, (px, pl, pc) in enumerate(p_items):
        box(ax, px, pipeline_y, 1.7, 0.6, pl, "", pc, 9)
        if i < len(p_items) - 1:
            arrow(ax, px + 0.85, pipeline_y, p_items[i+1][0] - 0.85,
                  pipeline_y, color=C["gray"], lw=1.5)

    # COMMIT
    box(ax, 11.5, pipeline_y, 1.7, 0.6, "COMMIT", "", C["success"], 9)
    arrow(ax, 10.8 + 0.85, pipeline_y, 11.5 - 0.85, pipeline_y,
          color=C["gray"], lw=1.5)

    # DB
    box(ax, 11.5, 1.5, 1.9, 0.65, "Wiki Pages\nPostgreSQL", "",
        C["success"], 8.5)
    arrow(ax, 11.5, pipeline_y - 0.3, 11.5, 1.83, color=C["success"])

    # Embedding
    box(ax, 8.5, 1.5, 2.0, 0.65, "Embeddings\npgvector", "",
        C["primary"], 8.5)
    arrow(ax, 11.5, 1.5, 10.5, 1.5, color=C["primary"])

    # Status update feedback
    ax.annotate("", xy=(3.5, 4.625), xytext=(3.5, 3.5),
                arrowprops=dict(arrowstyle="<-", color=hex_to_rgb(C["secondary"]),
                                lw=1.2, linestyle="dotted"))
    ax.text(3.5, 4.1, "status\nupdate", fontsize=7, ha="center",
            color=hex_to_rgb(C["secondary"]), fontfamily=VN_FONT)

    ax.text(6.5, 0.5,
            "source.pipeline_phase lưu phase cuối hoàn thành → Worker tự resume nếu crash",
            ha="center", fontsize=8.5, color=hex_to_rgb(C["gray"]),
            style="italic", fontfamily=VN_FONT,
            bbox=dict(boxstyle="round,pad=0.35", fc=hex_to_rgb(C["light"]),
                      ec=hex_to_rgb(C["gray"]), lw=0.8))

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 4 — RBAC Model
# ═══════════════════════════════════════════════════════════════════════════════
def make_rbac_diagram():
    fig, ax = plt.subplots(figsize=(13, 7))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 13); ax.set_ylim(0, 7)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("Mô hình phân quyền (Dual-Realm RBAC)", fontsize=15,
                 fontweight="bold", color=hex_to_rgb(C["primary"]),
                 pad=12, fontfamily=VN_FONT)

    # Global realm box
    gr = FancyBboxPatch((0.3, 0.3), 5.8, 6.2,
                         boxstyle="round,pad=0.1",
                         facecolor=hex_to_rgb("#E3F2FD"),
                         edgecolor=hex_to_rgb(C["primary"]), lw=2)
    ax.add_patch(gr)
    ax.text(3.2, 6.35, "GLOBAL REALM", ha="center", fontweight="bold",
            fontsize=11, color=hex_to_rgb(C["primary"]), fontfamily=VN_FONT)

    # Workspace realm box
    wr = FancyBboxPatch((6.9, 0.3), 5.8, 6.2,
                         boxstyle="round,pad=0.1",
                         facecolor=hex_to_rgb("#F3E5F5"),
                         edgecolor=hex_to_rgb(C["purple"]), lw=2)
    ax.add_patch(wr)
    ax.text(9.8, 6.35, "WORKSPACE REALM", ha="center", fontweight="bold",
            fontsize=11, color=hex_to_rgb(C["purple"]), fontfamily=VN_FONT)

    # Employee
    box(ax, 3.2, 5.4, 2.2, 0.6, "Employee", "", C["primary"], 9)

    # Department
    box(ax, 1.5, 4.0, 2.2, 0.6, "Department", "", C["secondary"], 9)
    arrow(ax, 2.6, 5.1, 1.8, 4.3, "belongs to")

    # Role (from dept)
    box(ax, 4.8, 4.0, 2.0, 0.6, "Role", "(dept preset)", C["secondary"], 9)
    arrow(ax, 3.8, 5.1, 4.6, 4.3, "inherits")

    # Permissions
    perms = [
        (1.0, 2.6, "doc:read\ndoc:write"),
        (2.8, 2.6, "wiki:read\nwiki:write"),
        (4.6, 2.6, "settings:read\nsettings:write"),
    ]
    for px, py, pl in perms:
        box(ax, px, py, 1.55, 0.7, pl, "", C["accent"], 8)
        arrow(ax, 4.8, 3.7, px, py + 0.35, color=C["accent"], lw=1)

    ax.text(3.0, 1.5, "Phòng ban → quyết định role → quyết định permissions",
            ha="center", fontsize=8, color=hex_to_rgb(C["gray"]),
            fontfamily=VN_FONT, style="italic")

    # Workspace side
    box(ax, 9.8, 5.4, 2.2, 0.6, "Workspace", "", C["purple"], 9)

    ws_roles = [
        (8.0, 3.9, "Viewer",      C["gray"]),
        (9.5, 3.9, "Contributor", C["secondary"]),
        (11.0, 3.9, "Editor",     C["primary"]),
        (12.5, 3.9, "Admin",      C["purple"]),
    ]
    for rx, ry, rl, rc in ws_roles:
        box(ax, rx, ry, 1.3, 0.55, rl, "", rc, 8)
        arrow(ax, 9.8, 5.1, rx, ry + 0.28, color=C["purple"], lw=1)

    # Scope arrows
    perm_ws = [
        (8.0, 2.5, "wiki:read"),
        (9.5, 2.5, "+draft"),
        (11.0, 2.5, "+write\n+approve"),
        (12.5, 2.5, "+manage\nmembers"),
    ]
    for i, (px, py, pl) in enumerate(perm_ws):
        box(ax, px, py, 1.3, 0.55, pl, "", C["accent"], 8)
        arrow(ax, ws_roles[i][0], ws_roles[i][1] - 0.28, px, py + 0.28,
              color=C["accent"], lw=1)

    # Independent note
    ax.text(6.5, 0.7, "Hai realm hoàn toàn độc lập — quyền workspace không ảnh hưởng global và ngược lại",
            ha="center", fontsize=8.5, color=hex_to_rgb(C["gray"]),
            style="italic", fontfamily=VN_FONT,
            bbox=dict(boxstyle="round,pad=0.35", fc="white",
                      ec=hex_to_rgb(C["gray"]), lw=0.8))

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 5 — Database Schema (key tables)
# ═══════════════════════════════════════════════════════════════════════════════
def make_db_schema():
    fig, ax = plt.subplots(figsize=(14, 8))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 14); ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("Database Schema — Các bảng chính (PostgreSQL + pgvector)", fontsize=14,
                 fontweight="bold", color=hex_to_rgb(C["primary"]),
                 pad=12, fontfamily=VN_FONT)

    def table_box(ax, x, y, w, title, fields, color):
        h_header = 0.45
        h_row = 0.32
        total_h = h_header + len(fields) * h_row + 0.15

        # header
        hd = FancyBboxPatch((x, y - h_header), w, h_header,
                             boxstyle="round,pad=0.03",
                             facecolor=hex_to_rgb(color), edgecolor="none")
        ax.add_patch(hd)
        ax.text(x + w/2, y - h_header/2, title, ha="center", va="center",
                fontsize=9, fontweight="bold", color="white",
                fontfamily=VN_FONT)

        # body
        bd = FancyBboxPatch((x, y - total_h), w, total_h - h_header,
                             boxstyle="round,pad=0.03",
                             facecolor="white",
                             edgecolor=hex_to_rgb(color), linewidth=1.2)
        ax.add_patch(bd)

        for i, (fname, ftype) in enumerate(fields):
            fy = y - h_header - (i + 0.5) * h_row - 0.07
            pk = "** " if "PK" in ftype else ("FK " if "FK" in ftype else "   ")
            ax.text(x + 0.12, fy, pk + fname, fontsize=7.5, va="center",
                    fontfamily=VN_FONT,
                    fontweight="bold" if "PK" in ftype else "normal",
                    color=hex_to_rgb(C["primary"] if "PK" in ftype else
                                      C["secondary"] if "FK" in ftype else C["gray"]))
            ax.text(x + w - 0.1, fy, ftype.replace("PK","").replace("FK","").strip(),
                    fontsize=6.5, va="center", ha="right",
                    fontfamily=VN_FONT, color=hex_to_rgb(C["gray"]))

        return total_h

    # employees
    table_box(ax, 0.2, 7.8, 3.2, "employees", [
        ("id",             "UUID PK"),
        ("email",          "VARCHAR"),
        ("full_name",      "VARCHAR"),
        ("department_id",  "UUID FK"),
        ("role",           "ENUM"),
        ("custom_role_id", "UUID FK"),
        ("is_active",      "BOOLEAN"),
    ], C["primary"])

    # departments
    table_box(ax, 4.0, 7.8, 3.2, "departments", [
        ("id",          "UUID PK"),
        ("name",        "VARCHAR"),
        ("role_preset", "VARCHAR"),
        ("permissions", "JSONB"),
    ], C["secondary"])

    # sources
    table_box(ax, 7.8, 7.8, 3.8, "sources", [
        ("id",                "UUID PK"),
        ("title",             "VARCHAR"),
        ("source_type",       "ENUM"),
        ("scope_type",        "ENUM"),
        ("knowledge_type_id", "UUID FK"),
        ("status",            "ENUM"),
        ("pipeline_phase",    "VARCHAR"),
        ("minio_key",         "VARCHAR"),
        ("notebooklm_status", "ENUM"),
    ], C["warning"])

    # wiki_pages
    table_box(ax, 0.2, 4.1, 3.5, "wiki_pages", [
        ("id",               "UUID PK"),
        ("slug",             "VARCHAR UNIQUE"),
        ("title",            "VARCHAR"),
        ("content_md",       "TEXT"),
        ("source_id",        "UUID FK"),
        ("scope_type",       "ENUM"),
        ("knowledge_type_id","UUID FK"),
        ("generated_by",     "VARCHAR"),
    ], C["accent"])

    # wiki_page_embeddings
    table_box(ax, 4.0, 4.1, 3.5, "wiki_page_embeddings_*", [
        ("id",          "UUID PK"),
        ("wiki_page_id","UUID FK"),
        ("embedding",   "vector(N)"),
        ("chunk_text",  "TEXT"),
        ("chunk_index", "INT"),
    ], C["accent"])

    # workspaces
    table_box(ax, 7.8, 4.1, 2.8, "workspaces", [
        ("id",          "UUID PK"),
        ("name",        "VARCHAR"),
        ("description", "TEXT"),
    ], C["purple"])

    # workspace_members
    table_box(ax, 11.0, 4.1, 2.8, "workspace_members", [
        ("workspace_id","UUID FK"),
        ("employee_id", "UUID FK"),
        ("role",        "ENUM"),
    ], C["purple"])

    # mcp_tokens
    table_box(ax, 0.2, 1.2, 3.2, "mcp_tokens", [
        ("id",                    "UUID PK"),
        ("employee_id",           "UUID FK"),
        ("token_hash",            "VARCHAR"),
        ("allowed_knowledge_types","JSONB"),
        ("expires_at",            "TIMESTAMP"),
    ], C["gray"])

    # app_config
    table_box(ax, 4.0, 1.2, 3.2, "app_config", [
        ("key",       "VARCHAR PK"),
        ("value",     "TEXT"),
        ("encrypted", "BOOLEAN"),
    ], C["gray"])

    # Relationships
    arrow(ax, 3.4, 7.15, 4.0, 7.4, color=C["secondary"])      # emp → dept
    arrow(ax, 7.8, 6.5, 3.7, 4.1, color=C["accent"])           # source → wiki_page (approx)
    arrow(ax, 3.7, 3.65, 4.0, 3.7, color=C["accent"])          # wiki_page → embedding
    arrow(ax, 10.8, 3.65, 11.0, 3.75, color=C["purple"])       # workspace → members

    # note about pgvector
    ax.text(7.0, 0.35,
            "wiki_page_embeddings_768 / _1024 / _1536 / _3072 — mỗi dimension-size là một bảng riêng với HNSW index",
            ha="center", fontsize=8, color=hex_to_rgb(C["gray"]),
            fontfamily=VN_FONT, style="italic",
            bbox=dict(boxstyle="round,pad=0.35", fc=hex_to_rgb(C["light"]),
                      ec=hex_to_rgb(C["gray"]), lw=0.8))

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 6 — MCP Integration
# ═══════════════════════════════════════════════════════════════════════════════
def make_mcp_diagram():
    fig, ax = plt.subplots(figsize=(13, 6))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 13); ax.set_ylim(0, 6)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("Tích hợp MCP — Claude ↔ Arkon Knowledge Base", fontsize=15,
                 fontweight="bold", color=hex_to_rgb(C["primary"]),
                 pad=12, fontfamily=VN_FONT)

    # Claude clients
    box(ax, 1.2, 4.8, 2.0, 0.65, "Claude Desktop", "", C["gray"], 9)
    box(ax, 1.2, 3.8, 2.0, 0.65, "Claude Code", "", C["gray"], 9)
    box(ax, 1.2, 2.8, 2.0, 0.65, "Claude.ai", "", C["gray"], 9)

    # MCP Token
    box(ax, 4.2, 3.8, 2.0, 0.65, "MCP Token\n(Bearer)", "", C["warning"], 9)
    for y in [4.8, 3.8, 2.8]:
        arrow(ax, 2.2, y, 3.2, 3.8 if y != 3.8 else y, color=C["gray"])

    arrow(ax, 5.2, 3.8, 6.1, 3.8, "Auth", C["warning"])

    # MCP Server
    box(ax, 7.2, 3.8, 2.2, 0.65, "FastMCP Server\n/mcp", "", C["purple"], 9)
    arrow(ax, 6.1, 3.8, 6.1, 3.8)

    # Tools
    tools = [
        (5.5, 2.2, "search_wiki"),
        (7.2, 2.2, "read_wiki_page"),
        (9.0, 2.2, "list_sources"),
        (10.7, 2.2, "get_source"),
        (5.5, 1.1, "propose_wiki_edit"),
        (7.2, 1.1, "edit_wiki_page"),
        (9.0, 1.1, "list_wiki_pages"),
        (10.7, 1.1, "get_skill"),
    ]
    for tx, ty, tl in tools:
        box(ax, tx, ty, 1.5, 0.5, tl, "", C["accent"], 7.5)
        arrow(ax, 7.2, 3.47, tx, ty + 0.25, color=C["accent"], lw=1)

    # DB
    box(ax, 11.5, 3.8, 1.4, 0.65, "PostgreSQL", "", C["success"], 9)
    arrow(ax, 8.3, 3.8, 10.8, 3.8, color=C["success"])

    # Scope note
    ax.text(6.5, 0.35,
            "Token scoping: allowed_knowledge_types + workspace memberships → RBAC enforced server-side",
            ha="center", fontsize=8.5, color=hex_to_rgb(C["gray"]),
            fontfamily=VN_FONT, style="italic",
            bbox=dict(boxstyle="round,pad=0.35", fc=hex_to_rgb(C["light"]),
                      ec=hex_to_rgb(C["purple"]), lw=0.8))

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DIAGRAM 7 — NotebookLM Integration
# ═══════════════════════════════════════════════════════════════════════════════
def make_notebooklm_diagram():
    fig, ax = plt.subplots(figsize=(14, 7))
    fig.patch.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_xlim(0, 14); ax.set_ylim(0, 7)
    ax.axis("off")
    ax.set_facecolor(hex_to_rgb(C["bg"]))
    ax.set_title("Luồng tích hợp NotebookLM", fontsize=15,
                 fontweight="bold", color=hex_to_rgb(C["primary"]),
                 pad=12, fontfamily=VN_FONT)

    steps = [
        (1.0, "User\ntrigger",       C["gray"]),
        (3.0, "API\n/sync",          C["secondary"]),
        (5.0, "Redis\nQueue",        C["danger"]),
        (7.0, "Worker\nTask",        C["accent"]),
        (9.2, "Download\nfrom MinIO",C["warning"]),
        (11.4,"NLM\nCreate NB",      "#1565C0"),
        (13.2,"NLM\nUpload",         "#1565C0"),
    ]

    for x, label, color in steps:
        box(ax, x, 5.3, 1.65, 0.75, label, "", color, 9)

    for i in range(len(steps) - 1):
        arrow(ax, steps[i][0] + 0.83, 5.3,
              steps[i+1][0] - 0.83, 5.3, color=C["gray"], lw=1.8)

    # Row 2 (continuing from NLM Upload)
    steps2 = [
        (13.2, "NLM Generate\nReport", "#1565C0"),
        (11.0, "NLM Download\nReport.md", "#1565C0"),
        (8.8,  "Parse\nMarkdown",    C["accent"]),
        (6.6,  "Upsert\nWiki Pages", C["success"]),
        (4.4,  "Update\nNLM Status", C["secondary"]),
    ]
    for x, label, color in steps2:
        box(ax, x, 3.3, 1.8, 0.75, label, "", color, 8.5)

    for i in range(len(steps2) - 1):
        arrow(ax, steps2[i][0] - 0.9, 3.3,
              steps2[i+1][0] + 0.9, 3.3, color=C["gray"], lw=1.8)

    # Connect row 1 → row 2
    arrow(ax, 13.2, 4.93, 13.2, 3.68, "wait\n5-15min", C["warning"], lw=1.5)

    # Connect to wiki
    box(ax, 4.4, 1.5, 2.0, 0.65, "Wiki Pages\nnlm/ slug", "", C["success"], 9)
    arrow(ax, 6.6, 2.93, 4.4, 2.0, color=C["success"])

    # Legend phases
    ax.text(7.0, 0.55,
            "Phase 1: Manual trigger  |  Phase 2: Auto-sync on upload  |  Phase 3: Podcast / Quiz",
            ha="center", fontsize=9, color=hex_to_rgb(C["gray"]),
            fontfamily=VN_FONT,
            bbox=dict(boxstyle="round,pad=0.35", fc=hex_to_rgb(C["light"]),
                      ec=hex_to_rgb(C["secondary"]), lw=0.8))

    # NLM cloud indicator
    nlm_box = FancyBboxPatch((10.2, 2.6), 3.5, 3.3,
                              boxstyle="round,pad=0.1",
                              facecolor=hex_to_rgb("#E8F5E9"),
                              edgecolor=hex_to_rgb("#1565C0"),
                              linewidth=1.5, linestyle="--", zorder=0)
    ax.add_patch(nlm_box)
    ax.text(11.95, 6.0, "Google NotebookLM", ha="center", fontsize=9,
            color=hex_to_rgb("#1565C0"), fontweight="bold", fontfamily=VN_FONT)

    return fig_to_bytes(fig)

# ═══════════════════════════════════════════════════════════════════════════════
# DOCX Builder
# ═══════════════════════════════════════════════════════════════════════════════

def setup_styles(doc: Document):
    styles = doc.styles

    # Normal text
    normal = styles["Normal"]
    normal.font.name = VN_FONT
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(4)

    def make_heading(name, size, color_hex, bold=True):
        try:
            s = styles[name]
        except KeyError:
            s = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        s.base_style = styles["Normal"]
        s.font.bold = bold
        s.font.size = Pt(size)
        rgb = tuple(int(color_hex.lstrip("#")[i:i+2], 16) for i in (0,2,4))
        s.font.color.rgb = RGBColor(*rgb)
        s.font.name = VN_FONT
        s.paragraph_format.space_before = Pt(size * 0.8)
        s.paragraph_format.space_after  = Pt(size * 0.4)
        return s

    make_heading("Heading 1", 18, C["primary"])
    make_heading("Heading 2", 14, C["secondary"])
    make_heading("Heading 3", 12, C["accent"])
    make_heading("Heading 4", 11, C["gray"])

    # Code
    try:
        code_style = styles["Code Block"]
    except KeyError:
        code_style = styles.add_style("Code Block", WD_STYLE_TYPE.PARAGRAPH)
    code_style.font.name = "Consolas"
    code_style.font.size = Pt(8.5)
    code_style.paragraph_format.space_after = Pt(2)
    code_style.paragraph_format.left_indent = Cm(0.5)

    # Table header
    try:
        th = styles["Table Header"]
    except KeyError:
        th = styles.add_style("Table Header", WD_STYLE_TYPE.CHARACTER)
    th.font.bold = True
    th.font.color.rgb = RGBColor(255, 255, 255)
    th.font.name = VN_FONT


def add_cover(doc: Document):
    # spacer
    for _ in range(4):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("ARKON")
    run.font.size = Pt(42)
    run.font.bold = True
    run.font.color.rgb = RGBColor(21, 101, 192)
    run.font.name = VN_FONT

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run("Enterprise AI Knowledge Base")
    r2.font.size = Pt(20)
    r2.font.color.rgb = RGBColor(2, 136, 209)
    r2.font.name = VN_FONT

    doc.add_paragraph()

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r3 = p3.add_run("Tài liệu Kiến trúc & Hướng dẫn vận hành")
    r3.font.size = Pt(14)
    r3.font.color.rgb = RGBColor(84, 110, 122)
    r3.font.name = VN_FONT

    doc.add_paragraph()

    p4 = doc.add_paragraph()
    p4.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r4 = p4.add_run("Phiên bản: 1.0  |  Tháng 5/2026")
    r4.font.size = Pt(11)
    r4.font.color.rgb = RGBColor(120, 120, 120)
    r4.font.name = VN_FONT

    doc.add_page_break()


def insert_image(doc: Document, img_bytes: io.BytesIO, width: float = 6.0, caption: str = ""):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(img_bytes, width=Inches(width))
    if caption:
        cp = doc.add_paragraph(caption)
        cp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cp.runs[0].font.italic = True
        cp.runs[0].font.size = Pt(9)
        cp.runs[0].font.color.rgb = RGBColor(100, 100, 100)


def add_info_box(doc: Document, text: str, color_hex: str = C["light"]):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.italic = True
    run.font.size = Pt(9.5)
    run.font.color.rgb = RGBColor(*tuple(int(color_hex.lstrip("#")[i:i+2], 16) for i in (0,2,4)))
    p.paragraph_format.left_indent  = Cm(0.8)
    p.paragraph_format.right_indent = Cm(0.8)


def md_to_docx(doc: Document, md_text: str, skip_h1: bool = False):
    """
    Minimal Markdown → python-docx converter.
    Handles: headings, code blocks, tables, bullet lists, bold.
    """
    lines = md_text.split("\n")
    in_code = False
    code_buf = []
    in_table = False

    def flush_code():
        nonlocal code_buf
        if code_buf:
            p = doc.add_paragraph("\n".join(code_buf), style="Code Block")
            pf = p.paragraph_format
            pf.left_indent = Cm(0.5)
            # light gray background via shading xml
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "F5F5F5")
            p._p.get_or_add_pPr().append(shd)
        code_buf = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # code block
        if line.startswith("```"):
            if not in_code:
                in_code = True
                code_buf = []
            else:
                in_code = False
                flush_code()
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # horizontal rule
        if re.match(r"^---+$", line.strip()):
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(4)
            i += 1
            continue

        # headings
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        if m:
            level = len(m.group(1))
            text  = m.group(2).strip()
            if level == 1 and skip_h1:
                i += 1
                continue
            style = {1:"Heading 1", 2:"Heading 2", 3:"Heading 3", 4:"Heading 4"}.get(level, "Heading 4")
            doc.add_paragraph(text, style=style)
            i += 1
            continue

        # table
        if line.startswith("|"):
            table_lines = []
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            # filter separator
            rows = [r for r in table_lines if not re.match(r"^\|[-| :]+\|$", r.strip())]
            if rows:
                cols = [c.strip() for c in rows[0].strip("|").split("|")]
                tbl = doc.add_table(rows=len(rows), cols=len(cols))
                tbl.style = "Table Grid"
                for ri, row_line in enumerate(rows):
                    cells = [c.strip() for c in row_line.strip("|").split("|")]
                    for ci, cell_text in enumerate(cells):
                        if ci < len(tbl.rows[ri].cells):
                            cell = tbl.rows[ri].cells[ci]
                            cell.text = re.sub(r"\*\*(.*?)\*\*", r"\1", cell_text)
                            p = cell.paragraphs[0]
                            p.runs[0].font.size = Pt(9)
                            p.runs[0].font.name = VN_FONT
                            if ri == 0:
                                p.runs[0].font.bold = True
                                # header bg
                                tc = cell._tc
                                tcPr = tc.get_or_add_tcPr()
                                shd = OxmlElement("w:shd")
                                shd.set(qn("w:val"), "clear")
                                shd.set(qn("w:color"), "auto")
                                shd.set(qn("w:fill"), "1565C0")
                                tcPr.append(shd)
                                p.runs[0].font.color.rgb = RGBColor(255, 255, 255)
            doc.add_paragraph()
            continue

        # bullet list
        m_bullet = re.match(r"^(\s*)[*\-]\s+(.*)", line)
        if m_bullet:
            indent = len(m_bullet.group(1))
            text = m_bullet.group(2)
            text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
            p = doc.add_paragraph(style="List Bullet")
            add_inline_runs(p, text)
            p.paragraph_format.left_indent = Cm(0.5 + indent * 0.3)
            i += 1
            continue

        # numbered list
        m_num = re.match(r"^\s*\d+\.\s+(.*)", line)
        if m_num:
            text = m_num.group(1)
            text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
            p = doc.add_paragraph(style="List Number")
            add_inline_runs(p, text)
            i += 1
            continue

        # blockquote
        if line.startswith(">"):
            text = line.lstrip("> ").strip()
            p = doc.add_paragraph(text)
            p.paragraph_format.left_indent  = Cm(0.8)
            p.runs[0].font.italic = True
            p.runs[0].font.color.rgb = RGBColor(84, 110, 122)
            i += 1
            continue

        # blank line
        if not line.strip():
            i += 1
            continue

        # normal paragraph
        p = doc.add_paragraph()
        add_inline_runs(p, line)
        i += 1

    if in_code:
        flush_code()


def add_inline_runs(p, text: str):
    """Handle **bold** and `inline code` within a paragraph."""
    parts = re.split(r"(\*\*.*?\*\*|`[^`]+`)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            r = p.add_run(part[2:-2])
            r.bold = True
            r.font.name = VN_FONT
        elif part.startswith("`") and part.endswith("`"):
            r = p.add_run(part[1:-1])
            r.font.name = "Consolas"
            r.font.size = Pt(9)
        else:
            r = p.add_run(part)
            r.font.name = VN_FONT


# ═══════════════════════════════════════════════════════════════════════════════
# Main build
# ═══════════════════════════════════════════════════════════════════════════════

def build_doc():
    doc = Document()

    # Page margins
    section = doc.sections[0]
    section.page_width  = Cm(21)
    section.page_height = Cm(29.7)
    section.left_margin   = Cm(2.5)
    section.right_margin  = Cm(2.5)
    section.top_margin    = Cm(2.5)
    section.bottom_margin = Cm(2.0)

    setup_styles(doc)

    # ── Cover ──────────────────────────────────────────────────────────────────
    print("[1/13] Cover page...")
    add_cover(doc)

    # ── Chapter 1: Architecture Overview ──────────────────────────────────────
    print("[2/13] Architecture overview...")
    doc.add_paragraph("Chương 1 — Kiến trúc Hệ thống", style="Heading 1")

    add_info_box(doc,
        "Arkon là nền tảng AI Knowledge Base dành cho doanh nghiệp. "
        "Hệ thống tự động xử lý tài liệu (PDF, DOCX, URL) thành wiki có cấu trúc "
        "thông qua pipeline MRP (Map-Reduce-Prompt) và cung cấp giao diện tìm kiếm "
        "ngữ nghĩa, tích hợp Claude qua MCP.", C["light"])
    doc.add_paragraph()

    print("  -> Generating architecture diagram...")
    insert_image(doc, make_architecture_diagram(), 6.5,
                 "Hình 1.1 — Kiến trúc tổng thể: 7 services chạy trong Docker Compose")

    doc.add_paragraph()
    doc.add_paragraph("Các thành phần chính", style="Heading 2")

    components = [
        ("Frontend (Next.js 15 :3119)",
         "Giao diện web cho người dùng. Dùng Server Components, App Router. "
         "Giao tiếp với backend qua API calls. Upload file bypass Next.js proxy, "
         "gửi thẳng đến port 5055 để tránh giới hạn kích thước."),
        ("API Server (FastAPI :5055)",
         "REST API backend. Xử lý authentication (JWT), file upload, wiki CRUD, "
         "settings, workspace management. Mount FastMCP tại /mcp. "
         "Chạy Alembic migrations khi khởi động."),
        ("Worker (arq)",
         "Background task queue. Chạy MRP pipeline (Triage→MAP→REDUCE→REFINE→VERIFY→COMMIT). "
         "Sử dụng advisory locks để tránh race condition. "
         "Lưu pipeline_phase sau mỗi phase → tự resume nếu crash."),
        ("Worker Skills (arq)",
         "Xử lý skill packages (.zip) riêng để không block document ingestion. "
         "Skill là tập lệnh cho phép Claude thực hiện các hành động tùy chỉnh."),
        ("PostgreSQL + pgvector",
         "Database chính. 23 bảng ORM. pgvector lưu embeddings đa chiều "
         "(768d, 1024d, 1536d, 3072d) với HNSW index cho semantic search."),
        ("Redis (arq queue)",
         "Job queue cho background tasks. Lưu trạng thái job, "
         "kết quả intermediate của pipeline."),
        ("MinIO (S3-compatible :9002)",
         "Object storage cho file gốc (PDF, DOCX) và ảnh được extract từ PDF. "
         "Image proxy tại /api/wiki/images/{uuid} để serve ảnh có authentication."),
    ]
    for name, desc in components:
        p = doc.add_paragraph(style="List Bullet")
        r = p.add_run(name + ": ")
        r.bold = True
        r.font.name = VN_FONT
        p.add_run(desc).font.name = VN_FONT

    doc.add_page_break()

    # ── Chapter 2: MRP Pipeline ────────────────────────────────────────────────
    print("[3/13] MRP Pipeline chapter...")
    doc.add_paragraph("Chương 2 — MRP Pipeline", style="Heading 1")

    add_info_box(doc,
        "MRP (Map-Reduce-Prompt) là pipeline 6 phase xử lý tài liệu thành wiki pages. "
        "Mỗi phase được thực hiện bởi worker, sử dụng LLM provider được cấu hình.", C["light"])
    doc.add_paragraph()

    print("  -> Generating MRP pipeline diagram...")
    insert_image(doc, make_mrp_pipeline(), 6.5,
                 "Hình 2.1 — MRP Pipeline: 6 phases từ tài liệu thô đến wiki pages")

    doc.add_paragraph()
    doc.add_paragraph("Mô tả các phase", style="Heading 2")

    phases_desc = [
        ("1. TRIAGE", C["accent"],
         "LLM phân tích tài liệu, tạo outline, quyết định pipeline strategy "
         "(standard / large_document / multi_topic). Kết quả: outline_json."),
        ("2. MAP", C["secondary"],
         "Chia tài liệu thành chunks, gửi từng chunk lên LLM để extract entities, "
         "facts, concepts. Chạy song song (concurrent). Kết quả: danh sách wiki page drafts."),
        ("3. REDUCE", C["primary"],
         "Gộp các drafts trùng slug, merge nội dung. Tạo wiki page hoàn chỉnh "
         "cho từng slug unique. Kết quả: wiki_page objects."),
        ("4. REFINE", C["purple"],
         "LLM cải thiện prose, loại bỏ trùng lặp, chuẩn hóa format Markdown. "
         "Áp dụng knowledge type description để định dạng phù hợp."),
        ("5. VERIFY", C["warning"],
         "Kiểm tra cross-reference, fact accuracy. Đảm bảo không có thông tin "
         "sai lệch hoặc mâu thuẫn giữa các wiki pages."),
        ("6. COMMIT", C["success"],
         "Upsert wiki pages vào PostgreSQL (dựa trên slug uniqueness). "
         "Enqueue embedding task cho semantic search. Cập nhật source.status = ready."),
    ]

    for phase_name, color, desc in phases_desc:
        p = doc.add_paragraph()
        r = p.add_run(f"  {phase_name}  ")
        r.bold = True
        r.font.name = VN_FONT
        rgb = tuple(int(color.lstrip("#")[i:i+2], 16) for i in (0,2,4))
        r.font.color.rgb = RGBColor(*rgb)
        p.add_run(desc).font.name = VN_FONT

    doc.add_paragraph()
    doc.add_paragraph("Plan Review Gate", style="Heading 3")
    doc.add_paragraph(
        "Khi MRP_AUTO_APPROVE_PLAN=false, pipeline dừng sau TRIAGE và chờ admin review. "
        "Admin xem outline_json, approve/reject trước khi MAP bắt đầu. "
        "Bật MRP_AUTO_APPROVE_PLAN=true để skip bước này."
    )

    doc.add_page_break()

    # ── Chapter 3: Upload Flow ─────────────────────────────────────────────────
    print("[4/13] Upload flow chapter...")
    doc.add_paragraph("Chương 3 — Luồng Upload & Xử lý Tài liệu", style="Heading 1")
    doc.add_paragraph()

    print("  -> Generating upload flow diagram...")
    insert_image(doc, make_upload_flow(), 6.5,
                 "Hình 3.1 — Luồng upload từ user đến wiki pages")

    doc.add_paragraph()
    doc.add_paragraph("Resume sau crash", style="Heading 2")
    doc.add_paragraph(
        "Trường source.pipeline_phase lưu phase cuối cùng đã hoàn thành. "
        "Khi worker restart (crash hoặc manual), hệ thống đọc pipeline_phase "
        "và tiếp tục từ phase tiếp theo — không xử lý lại từ đầu. "
        "Để reset thủ công: POST /api/sources/{id}/retry."
    )

    doc.add_paragraph("Các loại tài liệu hỗ trợ", style="Heading 2")
    file_types = [
        ("PDF", "Sử dụng pymupdf — hỗ trợ extract text và images"),
        ("DOCX", "Sử dụng python-docx + mammoth — hỗ trợ tables, headings"),
        ("URL", "Crawl và extract content từ web page"),
        ("YouTube", "Transcript extraction từ video"),
        ("Plain Text", "Xử lý trực tiếp"),
    ]
    for ft, desc in file_types:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(f"{ft}: ").bold = True
        p.runs[0].font.name = VN_FONT
        p.add_run(desc).font.name = VN_FONT

    doc.add_page_break()

    # ── Chapter 4: RBAC ────────────────────────────────────────────────────────
    print("[5/13] RBAC chapter...")
    doc.add_paragraph("Chương 4 — Phân quyền (Dual-Realm RBAC)", style="Heading 1")
    doc.add_paragraph()

    print("  -> Generating RBAC diagram...")
    insert_image(doc, make_rbac_diagram(), 6.0,
                 "Hình 4.1 — Mô hình phân quyền hai realm: Global và Workspace")

    doc.add_paragraph()
    doc.add_paragraph("Global Realm", style="Heading 2")
    doc.add_paragraph(
        "Quyền toàn hệ thống dựa trên Department → Role → Permissions. "
        "Mỗi phòng ban có một role preset với tập permissions cố định. "
        "Có thể tạo Custom Role để cấu hình permissions chi tiết hơn."
    )

    global_perms = [
        ("doc:read / doc:write",       "Đọc/upload tài liệu"),
        ("wiki:read / wiki:write:all", "Đọc/chỉnh sửa wiki"),
        ("settings:read / settings:write", "Xem/thay đổi cài đặt hệ thống"),
        ("admin:users",                "Quản lý nhân viên"),
        ("admin:workspaces",           "Quản lý workspaces"),
    ]
    tbl = doc.add_table(rows=len(global_perms)+1, cols=2)
    tbl.style = "Table Grid"
    hdr = tbl.rows[0]
    for ci, t in enumerate(["Permission", "Mô tả"]):
        cell = hdr.cells[ci]
        cell.text = t
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].runs[0].font.name = VN_FONT
        tc = cell._tc
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "1565C0")
        tc.get_or_add_tcPr().append(shd)
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255,255,255)
    for ri, (perm, desc) in enumerate(global_perms):
        row = tbl.rows[ri+1]
        row.cells[0].text = perm
        row.cells[0].paragraphs[0].runs[0].font.name = "Consolas"
        row.cells[0].paragraphs[0].runs[0].font.size = Pt(9)
        row.cells[1].text = desc
        row.cells[1].paragraphs[0].runs[0].font.name = VN_FONT

    doc.add_paragraph()
    doc.add_paragraph("Workspace Realm", style="Heading 2")
    doc.add_paragraph(
        "Workspace là không gian làm việc riêng biệt. "
        "Members có workspace role (Viewer/Contributor/Editor/Admin) hoàn toàn độc lập với global role. "
        "System admin luôn có full access mà không cần là member."
    )

    doc.add_page_break()

    # ── Chapter 5: Database Schema ─────────────────────────────────────────────
    print("[6/13] Database chapter...")
    doc.add_paragraph("Chương 5 — Database Schema", style="Heading 1")
    doc.add_paragraph()

    print("  -> Generating database diagram...")
    insert_image(doc, make_db_schema(), 6.5,
                 "Hình 5.1 — Các bảng chính trong PostgreSQL")

    doc.add_paragraph()
    doc.add_paragraph("pgvector — Semantic Search", style="Heading 2")
    doc.add_paragraph(
        "Hệ thống dùng pgvector với 4 bảng embedding riêng biệt theo số chiều: "
        "wiki_page_embeddings_768, _1024, _1536, _3072. "
        "Mỗi bảng có HNSW index (migration 015) cho tìm kiếm vector nhanh. "
        "Đổi embedding model không mất dữ liệu — chỉ cần Re-embed all pages "
        "để populate bảng mới."
    )

    doc.add_page_break()

    # ── Chapter 6: MCP Integration ─────────────────────────────────────────────
    print("[7/13] MCP chapter...")
    doc.add_paragraph("Chương 6 — MCP Server (Claude Integration)", style="Heading 1")
    doc.add_paragraph()

    print("  -> Generating MCP diagram...")
    insert_image(doc, make_mcp_diagram(), 6.2,
                 "Hình 6.1 — Tích hợp Claude Desktop/Code qua MCP protocol")

    doc.add_paragraph()
    doc.add_paragraph("14 MCP Tools", style="Heading 2")

    mcp_tools = [
        ("search_wiki",         "Tìm kiếm wiki bằng semantic search"),
        ("read_wiki_page",      "Đọc nội dung một wiki page theo slug"),
        ("list_wiki_pages",     "Liệt kê wiki pages với filter"),
        ("list_sources",        "Liệt kê tài liệu nguồn"),
        ("get_source",          "Đọc metadata một tài liệu"),
        ("propose_wiki_edit",   "Đề xuất chỉnh sửa wiki (tạo draft)"),
        ("edit_wiki_page",      "Chỉnh sửa trực tiếp (Editor+)"),
        ("get_skill",           "Đọc skill package"),
        ("list_skills",         "Liệt kê skills"),
        ("get_wiki_graph",      "Lấy knowledge graph"),
        ("list_knowledge_types","Liệt kê knowledge types"),
        ("get_workspace",       "Đọc workspace info"),
        ("list_workspaces",     "Liệt kê workspaces"),
        ("get_mcp_context",     "Thông tin token hiện tại"),
    ]
    tbl2 = doc.add_table(rows=len(mcp_tools)+1, cols=2)
    tbl2.style = "Table Grid"
    h = tbl2.rows[0]
    for ci, t in enumerate(["Tool", "Mô tả"]):
        cell = h.cells[ci]
        cell.text = t
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].runs[0].font.name = VN_FONT
        tc = cell._tc
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), "6A1B9A")
        tc.get_or_add_tcPr().append(shd)
        cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255,255,255)
    for ri, (tool, desc) in enumerate(mcp_tools):
        row = tbl2.rows[ri+1]
        row.cells[0].text = tool
        row.cells[0].paragraphs[0].runs[0].font.name = "Consolas"
        row.cells[0].paragraphs[0].runs[0].font.size = Pt(9)
        row.cells[1].text = desc
        row.cells[1].paragraphs[0].runs[0].font.name = VN_FONT

    doc.add_page_break()

    # ── Chapter 7: NotebookLM Integration ─────────────────────────────────────
    print("[8/13] NotebookLM chapter...")
    doc.add_paragraph("Chương 7 — Tích hợp NotebookLM (Kế hoạch)", style="Heading 1")

    add_info_box(doc,
        "Tích hợp NotebookLM cho phép người dùng upload tài liệu lên Google NotebookLM, "
        "tạo study guide/report chi tiết, và tự động đồng bộ vào Arkon wiki. "
        "Đây là enrichment layer bổ sung bên cạnh MRP pipeline hiện tại.", C["light"])
    doc.add_paragraph()

    print("  -> Generating NotebookLM diagram...")
    insert_image(doc, make_notebooklm_diagram(), 6.5,
                 "Hình 7.1 — Luồng tích hợp NotebookLM (3 phases)")

    doc.add_paragraph()
    doc.add_paragraph("3 Phases triển khai", style="Heading 2")

    nlm_phases = [
        ("Phase 1 — Manual Trigger",
         "Admin có thể trigger sync thủ công từ Knowledge table dropdown. "
         "NotebookLM service chạy dưới dạng arq background task. "
         "Wiki pages được tạo với slug prefix nlm/ riêng biệt."),
        ("Phase 2 — Auto-sync khi Upload",
         "Upload dialog có checkbox 'Sync to NotebookLM'. "
         "Sau khi ingest_file_task hoàn thành, tự động enqueue notebooklm_sync_task."),
        ("Phase 3 — Podcast / Quiz",
         "Mở rộng sang generate audio podcast, quiz JSON, slide deck PPTX. "
         "Lưu artifacts vào MinIO, play trong UI."),
    ]
    for ph_name, ph_desc in nlm_phases:
        p = doc.add_paragraph(style="List Bullet")
        r = p.add_run(ph_name + ": ")
        r.bold = True
        r.font.name = VN_FONT
        p.add_run(ph_desc).font.name = VN_FONT

    doc.add_paragraph()
    doc.add_paragraph("Thành phần mới cần tạo", style="Heading 3")
    nlm_components = [
        "app/services/notebooklm_service.py — CLI wrapper subprocess",
        "app/workers/notebooklm_tasks.py — arq background task",
        "app/routers/notebooklm.py — /api/notebooklm/* endpoints",
        "Migration 020 — thêm 5 cột vào bảng sources",
        "Frontend: dropdown action, status badge, Settings panel",
    ]
    for c in nlm_components:
        p = doc.add_paragraph(style="List Bullet")
        p.add_run(c).font.name = "Consolas"
        p.runs[0].font.size = Pt(9)

    doc.add_page_break()

    # ── Chapter 8: Quickstart ──────────────────────────────────────────────────
    print("[9/13] Quickstart chapter...")
    doc.add_paragraph("Chương 8 — Hướng dẫn Khởi động Nhanh", style="Heading 1")

    qs_path = DOCS_DIR / "QUICKSTART.md"
    if qs_path.exists():
        md_to_docx(doc, qs_path.read_text(encoding="utf-8"), skip_h1=True)

    doc.add_page_break()

    # ── Chapter 9: Admin Guide ─────────────────────────────────────────────────
    print("[10/13] Admin guide chapter...")
    doc.add_paragraph("Chương 9 — Hướng dẫn Quản trị", style="Heading 1")

    ag_path = DOCS_DIR / "ADMIN-GUIDE.md"
    if ag_path.exists():
        md_to_docx(doc, ag_path.read_text(encoding="utf-8"), skip_h1=True)

    doc.add_page_break()

    # ── Chapter 10: Knowledge Types ────────────────────────────────────────────
    print("[11/13] Knowledge types chapter...")
    doc.add_paragraph("Chương 10 — Knowledge Types", style="Heading 1")

    kt_path = DOCS_DIR / "KNOWLEDGE-TYPES.md"
    if kt_path.exists():
        md_to_docx(doc, kt_path.read_text(encoding="utf-8"), skip_h1=True)

    doc.add_page_break()

    # ── Chapter 11: Workspaces ─────────────────────────────────────────────────
    print("[12/13] Workspaces chapter...")
    doc.add_paragraph("Chương 11 — Workspaces", style="Heading 1")

    ws_path = DOCS_DIR / "WORKSPACES.md"
    if ws_path.exists():
        md_to_docx(doc, ws_path.read_text(encoding="utf-8"), skip_h1=True)

    doc.add_page_break()

    # ── Chapter 12: Troubleshooting ────────────────────────────────────────────
    print("[13/13] Troubleshooting chapter...")
    doc.add_paragraph("Chương 12 — Xử lý sự cố", style="Heading 1")

    ts_path = DOCS_DIR / "TROUBLESHOOTING.md"
    if ts_path.exists():
        md_to_docx(doc, ts_path.read_text(encoding="utf-8"), skip_h1=True)

    doc.add_page_break()

    # ── Chapter 13: API Reference (summary) ────────────────────────────────────
    print("[14] API Reference chapter...")
    doc.add_paragraph("Chương 13 — API Reference (tóm tắt)", style="Heading 1")

    api_summary = [
        ("Authentication", [
            ("POST /api/auth/login",          "Đăng nhập → JWT token"),
            ("POST /api/auth/refresh",         "Refresh token"),
            ("GET  /api/auth/me",              "Thông tin user hiện tại"),
        ]),
        ("Sources (Tài liệu)", [
            ("POST /api/sources/upload",       "Upload file mới"),
            ("GET  /api/sources",              "Liệt kê tài liệu (filter/pagination)"),
            ("GET  /api/sources/{id}",         "Chi tiết một tài liệu"),
            ("POST /api/sources/{id}/retry",   "Retry ingest từ đầu"),
            ("DELETE /api/sources/{id}",       "Xóa tài liệu"),
        ]),
        ("Wiki", [
            ("GET  /api/wiki/pages",           "Liệt kê wiki pages"),
            ("GET  /api/wiki/pages/{slug}",    "Đọc wiki page"),
            ("PUT  /api/wiki/pages/{slug}",    "Cập nhật wiki page (Editor+)"),
            ("GET  /api/wiki/search",          "Semantic search"),
            ("GET  /api/wiki/images/{uuid}",   "Proxy ảnh có auth"),
        ]),
        ("Admin", [
            ("GET  /api/employees",            "Liệt kê nhân viên"),
            ("POST /api/employees",            "Tạo nhân viên"),
            ("GET  /api/departments",          "Liệt kê phòng ban"),
            ("GET  /api/workspaces",           "Liệt kê workspaces"),
            ("POST /api/workspaces",           "Tạo workspace"),
        ]),
        ("Settings & MCP", [
            ("GET  /api/settings",             "Đọc cài đặt hệ thống"),
            ("PUT  /api/settings",             "Cập nhật cài đặt"),
            ("POST /api/settings/test-llm",    "Test LLM provider"),
            ("GET  /api/mcp/tokens",           "Liệt kê MCP tokens"),
            ("POST /api/mcp/tokens",           "Tạo MCP token mới"),
            ("/mcp",                           "FastMCP endpoint (SSE)"),
        ]),
    ]

    for group, endpoints in api_summary:
        doc.add_paragraph(group, style="Heading 2")
        tbl = doc.add_table(rows=len(endpoints)+1, cols=2)
        tbl.style = "Table Grid"
        h = tbl.rows[0]
        for ci, t in enumerate(["Endpoint", "Mô tả"]):
            cell = h.cells[ci]
            cell.text = t
            cell.paragraphs[0].runs[0].bold = True
            cell.paragraphs[0].runs[0].font.name = VN_FONT
            tc = cell._tc
            shd = OxmlElement("w:shd")
            shd.set(qn("w:val"), "clear")
            shd.set(qn("w:color"), "auto")
            shd.set(qn("w:fill"), "0288D1")
            tc.get_or_add_tcPr().append(shd)
            cell.paragraphs[0].runs[0].font.color.rgb = RGBColor(255,255,255)
        for ri, (ep, desc) in enumerate(endpoints):
            row = tbl.rows[ri+1]
            row.cells[0].text = ep
            row.cells[0].paragraphs[0].runs[0].font.name = "Consolas"
            row.cells[0].paragraphs[0].runs[0].font.size = Pt(8.5)
            row.cells[1].text = desc
            row.cells[1].paragraphs[0].runs[0].font.name = VN_FONT
        doc.add_paragraph()

    # ── Save ───────────────────────────────────────────────────────────────────
    print(f"\n Saving to {OUTPUT}...")
    doc.save(str(OUTPUT))
    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"Done! File: {OUTPUT}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    build_doc()
