# VENV
# /home/ubuntu/.venv
# cd /home/ubuntu/
# source .venv/bin/activate

# SERVICES
# sudo systemctl daemon-reload
# sudo systemctl restart portal-radiologico
# sudo systemctl status portal-radiologico --no-pager -l

# app.py
import os, json, uuid, ast, shutil, time, base64
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import (
    Flask, request, session, redirect, url_for,
    send_from_directory, jsonify, render_template_string, has_request_context
)

import dash
from dash import html, dcc, Input, Output, State, ALL, no_update
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc

# --------------------------------------
# Config & storage
# --------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DATA_DIR.mkdir(exist_ok=True, parents=True)
UPLOAD_DIR.mkdir(exist_ok=True, parents=True)

USERS_JSON   = DATA_DIR / "users.json"
DOCTORS_JSON = DATA_DIR / "doctors.json"
EXAMS_JSON   = DATA_DIR / "exams.json"
CATALOG_JSON = DATA_DIR / "exam_catalog.json"
THEME_JSON   = DATA_DIR / "theme.json"
AUDIT_JSON   = DATA_DIR / "audit_log.json"

# --------------------------------------
# Utils
# --------------------------------------
def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()

def load_json(path: Path, default):
    if not path.exists():
        save_json(path, default)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

def ensure_list_dicts(data):
    if isinstance(data, dict):
        data = list(data.values())
    if not isinstance(data, list):
        return []
    return [x for x in data if isinstance(x, dict)]

# --------------------------------------
# Seed inicial
# --------------------------------------
def seed_if_needed():
    if not USERS_JSON.exists():
        users = [
            {"id": 1, "nome": "Administrador", "email": "admin@local",
             "senha": "admin123", "ativo": True, "role": "admin"}
        ]
        save_json(USERS_JSON, users)
    if not DOCTORS_JSON.exists():
        doctors = [
            {"id": 1, "nome": "Dr. Ana Silva"},
            {"id": 2, "nome": "Dr. Bruno Lima"},
            {"id": 3, "nome": "Dra. Carla Moreira"},
        ]
        save_json(DOCTORS_JSON, doctors)
    if not CATALOG_JSON.exists():
        catalog = {
            "Tomografia": ["Abdômen", "Crânio", "Tórax", "Coluna"],
            "Ressonância": ["Abdômen", "Encéfalo", "Joelho"],
            "Raio-X": ["Tórax PA", "Mão", "Coluna Lombar"]
        }
        save_json(CATALOG_JSON, catalog)
    if not EXAMS_JSON.exists():
        save_json(EXAMS_JSON, [])
    if not THEME_JSON.exists():
        theme = {
            "portal_name": "Portal Radiológico",
            "logo_path": None,   # ex.: "uploads/logo.png"
            "theme": "light"     # light | dark | slate
        }
        save_json(THEME_JSON, theme)
    if not AUDIT_JSON.exists():
        save_json(AUDIT_JSON, [])

seed_if_needed()

# Reparo automático de users.json
def repair_users_file_if_needed():
    data = load_json(USERS_JSON, [])
    ok = isinstance(data, list) and all(isinstance(x, dict) for x in data)
    if ok:
        return
    try:
        shutil.copy2(USERS_JSON, USERS_JSON.with_suffix(f".json.bak-{int(time.time())}"))
    except Exception:
        pass
    if isinstance(data, dict):
        data = [v for v in data.values() if isinstance(v, dict)]
    if not isinstance(data, list):
        data = []
    data = [x for x in data if isinstance(x, dict)]
    if not data:
        data = [{"id": 1, "nome": "Administrador", "email": "admin@local",
                 "senha": "admin123", "ativo": True, "role": "admin"}]
    save_json(USERS_JSON, data)

repair_users_file_if_needed()

# --------------------------------------
# Domain helpers
# --------------------------------------
def list_users():
    data = ensure_list_dicts(load_json(USERS_JSON, []))
    for x in data:
        if "id" in x:
            try: x["id"] = int(x["id"])
            except: pass
    return data

def save_users(users):
    save_json(USERS_JSON, users)

def add_user(nome, email, senha, role="user", ativo=True):
    users = list_users()
    new_id = (max([u["id"] for u in users]) + 1) if users else 1
    users.append({"id": new_id, "nome": nome, "email": email, "senha": senha, "role": role, "ativo": bool(ativo)})
    save_users(users)
    return new_id

def update_user(uid, patch):
    users = list_users()
    changed = False
    for u in users:
        if u.get("id")==uid:
            u.update(patch)
            changed = True
            break
    if changed:
        save_users(users)
    return changed

def get_user_by_email(email):
    return next((u for u in list_users() if u.get("email")==email), None)

def current_user():
    if not has_request_context():
        return None
    users = list_users()
    uid = session.get("user_id")
    if uid is None:
        return None
    # aceitar id como int/str e compat por email
    uid_int = None
    if isinstance(uid, int):
        uid_int = uid
    elif isinstance(uid, str):
        if uid.isdigit(): uid_int = int(uid)
        else:
            for u in users:
                if u.get("email") == uid:
                    return u
    for u in users:
        if u.get("id") == uid_int:
            return u
    return None

def is_admin():
    u = current_user()
    return bool(u and u.get("role")=="admin" and u.get("ativo", True))

def list_doctors():
    return ensure_list_dicts(load_json(DOCTORS_JSON, []))

def add_doctor(nome: str):
    docs = list_doctors()
    new_id = (max([d["id"] for d in docs]) + 1) if docs else 1
    docs.append({"id": new_id, "nome": nome})
    save_json(DOCTORS_JSON, docs)
    return new_id

def update_doctor(did, patch):
    docs = list_doctors()
    changed=False
    for d in docs:
        if d.get("id")==did:
            d.update(patch); changed=True; break
    if changed: save_json(DOCTORS_JSON, docs)
    return changed

def delete_doctor(did):
    docs = list_doctors()
    new_docs = [d for d in docs if d.get("id")!=did]
    if len(new_docs)!=len(docs):
        save_json(DOCTORS_JSON, new_docs); return True
    return False

def list_catalog():
    data = load_json(CATALOG_JSON, {})
    if not isinstance(data, dict): data = {}
    return data

def save_catalog(cat):
    save_json(CATALOG_JSON, cat)

def list_exams():
    return ensure_list_dicts(load_json(EXAMS_JSON, []))

def add_exam(rec: dict):
    rows = list_exams()
    new_id = (max([x["id"] for x in rows]) + 1) if rows else 1
    rec["id"] = new_id
    rec["created_at"] = utcnow_iso()
    rows.append(rec)
    save_json(EXAMS_JSON, rows)
    return new_id

def update_exam(exam_id: int, patch: dict):
    rows = list_exams()
    changed = False
    for r in rows:
        if r.get("id")==exam_id:
            r.update(patch)
            r["updated_at"] = utcnow_iso()
            changed = True
            break
    if changed:
        save_json(EXAMS_JSON, rows)
    return changed

def delete_exam(exam_id: int):
    rows = list_exams()
    new_rows = [r for r in rows if r.get("id")!=exam_id]
    if len(new_rows)!=len(rows):
        save_json(EXAMS_JSON, new_rows)
        return True
    return False

def log_action(user_email, action, entity, entity_id, before=None, after=None):
    logs = ensure_list_dicts(load_json(AUDIT_JSON, []))
    logs.append({
        "ts": utcnow_iso(),
        "user": user_email,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "before": before,
        "after": after
    })
    save_json(AUDIT_JSON, logs)

def theme_get():
    data = load_json(THEME_JSON, {"portal_name":"Portal Radiológico","logo_path":None,"theme":"light"})
    if not isinstance(data, dict):
        data = {"portal_name":"Portal Radiológico","logo_path":None,"theme":"light"}
    return data

def theme_set(patch: dict):
    t = theme_get()
    t.update(patch)
    save_json(THEME_JSON, t)
    return t

def theme_logo_url():
    theme = theme_get()
    lp = theme.get("logo_path")
    if lp:
        if lp.startswith("uploads/"):
            return f"/{lp}"
        return f"/uploads/{lp}"
    return None

# --------------------------------------
# Flask
# --------------------------------------
server = Flask(__name__)
server.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            nxt = request.path or "/app/"
            return redirect(url_for("login", next=nxt))
        return fn(*args, **kwargs)
    return wrapper

@server.before_request
def protect_dash():
    p = request.path or ""
    if p.startswith("/app"):
        if not session.get("user_id"):
            return redirect(url_for("login", next="/app/"))

@server.route("/")
def index():
    return redirect("/app/")

@server.route("/health")
def health():
    return jsonify({"status":"ok","time": utcnow_iso()})

@server.route("/uploads/<path:fname>")
def uploads(fname):
    return send_from_directory(UPLOAD_DIR, fname)

# --- Login template (logo + campos)
LOGIN_TEMPLATE = """
<!doctype html><html><head><meta charset="utf-8">
<title>{{ portal_name }} - Login</title><meta name="viewport" content="width=device-width, initial-scale=1">
<link id="theme_login" rel="stylesheet" href="{{ theme_url }}">
<style>
html,body{height:100%;margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial}
.wrap{display:flex;align-items:center;justify-content:center;height:100%;background:#f6f7fb}
.card{background:#fff;padding:32px;border-radius:16px;width:min(92vw, 380px);box-shadow:0 10px 30px rgba(0,0,0,.08)}
.brand{display:flex;align-items:center;justify-content:center;margin-bottom:16px}
.brand img{height:clamp(72px, 18vh, 128px);width:auto;display:block}
h1{font-size:20px;margin:0 0 6px}
label{display:block;margin-top:12px;font-size:14px}
input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #dcdce6;margin-top:6px}
button{margin-top:16px;width:100%;padding:12px;border-radius:12px;border:0;background:#111827;color:#fff;font-weight:600}
.error{color:#b91c1c;font-size:13px;margin-top:8px}.hint{margin-top:12px;font-size:12px;color:#6b7280}
</style></head><body><div class="wrap"><div class="card">
<div class="brand">
  {% if logo_url %}
    <img src="{{ logo_url }}" alt="Logo">
  {% else %}
    <img src="https://dummyimage.com/120x120/eeeeee/aaaaaa.png&text=LOGO" alt="Logo">
  {% endif %}
</div>
<h1>Login</h1>{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post"><label>E-mail</label><input name="email" type="email" required autofocus>
<label>Senha</label><input name="senha" type="password" required><button type="submit">Entrar</button></form>
<div class="hint">Usuário inicial: <b>admin@local</b> / <b>admin123</b></div></div></div></body></html>
"""

@server.route("/login", methods=["GET","POST"])
def login():
    th = theme_get()
    error = None
    if request.method=="POST":
        email = (request.form.get("email") or "").strip().lower()
        senha = request.form.get("senha") or ""
        u = get_user_by_email(email)
        if u and u.get("senha")==senha and u.get("ativo", True):
            session["user_id"] = u["id"]
            session["user_email"] = u["email"]
            session["user_name"] = u.get("nome")
            nxt = request.args.get("next") or "/app/"
            return redirect(nxt)
        error = "Credenciais inválidas ou usuário inativo."
    return render_template_string(
        LOGIN_TEMPLATE,
        portal_name = th.get("portal_name") or "Portal Radiológico",
        logo_url   = theme_logo_url(),
        theme_url  = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
        error      = error
    )

@server.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# --------------------------------------
# Dash App
# --------------------------------------
external_stylesheets = [dbc.themes.BOOTSTRAP]
dash_app = dash.Dash(
    __name__,
    server=server,
    url_base_pathname="/app/",
    external_stylesheets=external_stylesheets,
    suppress_callback_exceptions=True
)
dash_app.title = "Portal Radiológico"

# --------- THEME helper (CSS dinâmico simples)
def theme_css_block():
    t = theme_get().get("theme","light")
    # Estilos simples de exemplo (override leve)
    if t == "dark":
        return html.Style("""
            body { background:#0f172a !important; color:#e5e7eb !important; }
            .navbar, .card, table { background:#111827 !important; color:#e5e7eb !important; }
            .border-bottom { border-color:#1f2937 !important; }
        """)
    if t == "slate":
        return html.Style("""
            body { background:#f1f5f9 !important; }
            .navbar { background:#e2e8f0 !important; }
            .card, table { background:#fff !important; }
        """)
    # light (padrão) → sem override pesado
    return html.Style("")

# --------- UI helpers
def br_datetime_label(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""

def user_header():
    u = current_user()
    name = (u.get("nome") if u else "Usuário")
    email = (u.get("email") if u else "")
    return dbc.Navbar(
        dbc.Container([
            html.Div(),  # sem título; só menu à direita
            dbc.DropdownMenu(
                label=f"{name}",
                children=[
                    dbc.DropdownMenuItem(email or "—", disabled=True),
                    dbc.DropdownMenuItem(divider=True),
                    dbc.DropdownMenuItem("Trocar senha", id="open_pw_modal"),
                    dbc.DropdownMenuItem("Sair", href="/logout", id="logout_btn"),
                ],
                color="secondary",
                className="ms-auto"
            )
        ], fluid=True),
        color="light", className="border-bottom shadow-sm navbar", sticky="top"
    )

MODALIDADES = list(list_catalog().keys())

def cadastro_card():
    return dbc.Card([
        dbc.CardHeader("Cadastro de Exame (Atendimento)", className="fw-semibold"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(dbc.Input(id="exam_id", placeholder="ID do exame", type="text"), md=3),
                dbc.Col(dcc.Dropdown(id="modalidade", options=[{"label":m,"value":m} for m in MODALIDADES], placeholder="Modalidade"), md=3),
                dbc.Col(dmc.Autocomplete(id="exame_auto", placeholder="Exame (catálogo ou digite)", data=[], limit=50), md=6),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col(dmc.DateTimePicker(
                    id="data_dt",
                    placeholder="Selecione data e hora",
                    valueFormat="DD/MM/YYYY HH:mm",
                    withSeconds=False
                ), md=6),
                dbc.Col(dmc.Autocomplete(
                    id="medico_auto",
                    placeholder="Médico responsável",
                    data=[], limit=100
                ), md=6),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col(dbc.Input(id="idade", placeholder="Idade", type="number", min=0, max=120), md=3),
                dbc.Col(dbc.Checklist(id="contraste_usado", options=[{"label":" Usou contraste","value":"yes"}], value=[]), md=3),
                dbc.Col(dbc.Input(id="contraste_qtd", placeholder="Qtd Contraste (mL)", type="number", min=0, step=1, disabled=True), md=3),
                dbc.Col(html.Div(), md=3)
            ], className="mb-2"),
            html.Hr(),
            dbc.Row(
                dbc.Col(
                    dbc.Button("Salvar Exame", id="btn_salvar", color="primary", className="px-4 py-2"),
                    width="auto", class_name="text-center"
                ),
                justify="center", class_name="mt-1"
            ),
            html.Div(id="save_feedback", className="mt-3"),
        ])
    ], className="shadow-sm card")

def exams_table_component(rows):
    if not rows:
        return dbc.Alert("Nenhum exame cadastrado.", color="secondary")
    header = html.Thead(html.Tr([
        html.Th("ID"), html.Th("Exame ID"), html.Th("Modalidade"), html.Th("Exame"), html.Th("Médico"),
        html.Th("Data/Hora"), html.Th("Idade"), html.Th("Contraste"), html.Th("Qtd (mL)"), html.Th("")
    ]))
    body = []
    for r in rows:
        body.append(html.Tr([
            html.Td(r.get("id")),
            html.Td(r.get("exam_id")),
            html.Td(r.get("modalidade")),
            html.Td(r.get("exame")),
            html.Td(r.get("medico")),
            html.Td(br_datetime_label(r.get("data_hora"))),
            html.Td(r.get("idade")),
            html.Td("Sim" if r.get("contraste_usado") else "Não"),
            html.Td(r.get("contraste_qtd")),
            html.Td(dbc.ButtonGroup([
                dbc.Button("Editar", id={"type":"edit_btn","id":r.get("id")}, size="sm", color="secondary", outline=True, className="me-1"),
                dbc.Button("Excluir", id={"type":"del_btn","id":r.get("id")}, size="sm", color="danger", outline=True),
            ], size="sm"))
        ]))
    return dbc.Table([header, html.Tbody(body)], striped=True, bordered=True, hover=True, responsive=True, className="bg-white table")

def exams_tab():
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    return dbc.Container([
        html.H4("Exames cadastrados"),
        html.Div(id="exams_feedback"),
        html.Div(id="exams_table", children=exams_table_component(rows)),
    ], fluid=True)

# --------- Gerencial sub-abas
def gerencial_users():
    if not is_admin():
        return dbc.Alert("Acesso restrito ao administrador.", color="danger")
    users = list_users()
    rows = []
    for u in users:
        rows.append(html.Tr([
            html.Td(u.get("id")), html.Td(u.get("nome")), html.Td(u.get("email")),
            html.Td(u.get("role")), html.Td("Ativo" if u.get("ativo") else "Inativo"),
            html.Td(dbc.ButtonGroup([
                dbc.Button("Editar", id={"type":"edit_user_btn","id":u.get("id")}, size="sm", color="secondary", outline=True, className="me-1"),
                dbc.Button("Ativar" if not u.get("ativo") else "Desativar",
                           id={"type":"toggle_user_btn","id":u.get("id")}, size="sm",
                           color="warning", outline=True),
            ], size="sm"))
        ]))
    table = dbc.Table([html.Thead(html.Tr([
        html.Th("ID"), html.Th("Nome"), html.Th("E-mail"), html.Th("Perfil"), html.Th("Status"), html.Th("")
    ])), html.Tbody(rows)], striped=True, bordered=True, hover=True, responsive=True, className="bg-white table")
    form = dbc.Card([
        dbc.CardHeader("Novo Usuário"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(dbc.Input(id="nu_nome", placeholder="Nome completo"), md=4),
                dbc.Col(dbc.Input(id="nu_email", placeholder="E-mail", type="email"), md=4),
                dbc.Col(dbc.Input(id="nu_senha", placeholder="Senha", type="password"), md=4),
            ], className="mb-2"),
            dbc.Row([
                dbc.Col(dcc.Dropdown(id="nu_role", options=[
                    {"label":"Usuário","value":"user"},
                    {"label":"Administrador","value":"admin"}
                ], placeholder="Perfil"), md=4),
                dbc.Col(dbc.Checklist(id="nu_ativo", options=[{"label":" Ativo","value":"yes"}], value=["yes"]), md=4),
                dbc.Col(dbc.Button("Criar usuário", id="nu_criar", color="primary"), md=4),
            ]),
            html.Div(id="nu_feedback", className="mt-2")
        ])
    ], className="mb-3 card")
    return html.Div([form, table, html.Div(id="users_feedback")])

def gerencial_doctors():
    docs = list_doctors()
    list_items = html.Ul([
        html.Li([
            html.Span(d.get("nome")),
            dbc.Button("Excluir", id={"type":"del_doc_btn","id":d.get("id")}, size="sm", color="danger", outline=True, className="ms-2")
        ]) for d in docs
    ], id="docs_list", className="mt-3")
    return html.Div([
        dbc.Card([
            dbc.CardHeader("Médicos"),
            dbc.CardBody([
                dmc.TextInput(id="novo_medico", placeholder="Nome do médico"),
                dmc.Button("Adicionar", id="btn_add_medico", mt=10),
                html.Div(id="docs_feedback", className="mt-2"),
                list_items
            ])
        ], className="card")
    ])

def gerencial_catalog():
    cat = list_catalog()
    modalidades = sorted(cat.keys())
    return html.Div([
        dbc.Card([
            dbc.CardHeader("Catálogo de Exames por Modalidade"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dcc.Dropdown(id="cat_modalidade", options=[{"label":m,"value":m} for m in modalidades],
                                         placeholder="Selecione a modalidade"), md=6),
                    dbc.Col(dbc.Input(id="cat_nova_modalidade", placeholder="Nova modalidade (opcional)"), md=4),
                    dbc.Col(dbc.Button("Criar modalidade", id="cat_criar_mod", color="secondary"), md=2)
                ], className="mb-3"),
                dbc.Row([
                    dbc.Col(dmc.TextInput(id="cat_exame_nome", placeholder="Ex.: Abdômen"), md=8),
                    dbc.Col(dbc.Button("Adicionar exame", id="cat_add_exame", color="primary"), md=4)
                ]),
                html.Div(id="cat_feedback", className="mt-2"),
                html.Hr(),
                html.Div(id="cat_lista", children=cat_list_component(cat, selected=None))
            ])
        ], className="card")
    ])

def cat_list_component(cat_dict, selected):
    blocks=[]
    for m in sorted(cat_dict.keys()):
        exames = cat_dict.get(m, [])
        items = []
        for ex in sorted(exames):
            items.append(html.Li([
                html.Span(ex),
                dbc.Button("Excluir", id={"type":"cat_del_ex","mod":m,"name":ex}, size="sm", color="danger", outline=True, className="ms-2")
            ]))
        blocks.append(dbc.Row([
            dbc.Col(html.H6(m), md=3),
            dbc.Col(html.Ul(items))
        ], className="mb-2"))
    return html.Div(blocks)

def gerencial_theme():
    th = theme_get()
    logo = theme_logo_url()
    preview = html.Div([
        html.H6("Pré-visualização"),
        dbc.Row([
            dbc.Col(dbc.Card([dbc.CardHeader("Card"), dbc.CardBody("Conteúdo de exemplo.")], className="mb-2 card"), md=4),
            dbc.Col(dbc.Table([html.Thead(html.Tr([html.Th("Col A"), html.Th("Col B")])),
                               html.Tbody([html.Tr([html.Td("1"), html.Td("2")])])],
                              className="table bg-white"), md=8)
        ])
    ], className="mt-3")
    return html.Div([
        dbc.Card([
            dbc.CardHeader("Tema do Portal"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dbc.Input(id="theme_portal_name", value=th.get("portal_name","Portal Radiológico"), placeholder="Nome do portal"), md=6),
                    dbc.Col(dcc.Dropdown(
                        id="theme_choice",
                        options=[
                            {"label":"Claro (padrão)","value":"light"},
                            {"label":"Escuro","value":"dark"},
                            {"label":"Slate","value":"slate"},
                        ], value=th.get("theme","light")), md=3),
                    dbc.Col(dbc.Button("Salvar tema", id="theme_save", color="primary"), md=3),
                ], className="mb-3"),
                dbc.Row([
                    dbc.Col([
                        html.Label("Logo (PNG/JPG)"),
                        dcc.Upload(
                            id="theme_logo_upload",
                            children=html.Div(["Arraste/solte ou ", html.A("clique para enviar")]),
                            multiple=False,
                            accept="image/*",
                            className="p-3 border rounded",
                        ),
                        html.Small("Atual: ", className="text-muted"),
                        html.Div(html.Img(src=logo, style={"maxHeight":"80px"}) if logo else "—", id="theme_logo_preview", className="mt-2")
                    ], md=6),
                    dbc.Col(preview, md=6)
                ]),
                html.Div(id="theme_feedback", className="mt-2")
            ])
        ], className="card")
    ])

def gerencial_logs():
    logs = ensure_list_dicts(load_json(AUDIT_JSON, []))
    rows=[]
    for l in reversed(logs[-500:]):  # últimas 500
        rows.append(html.Tr([
            html.Td(l.get("ts","")),
            html.Td(l.get("user","")),
            html.Td(l.get("action","")),
            html.Td(l.get("entity","")),
            html.Td(l.get("entity_id","")),
        ]))
    table = dbc.Table([html.Thead(html.Tr([
        html.Th("Quando"), html.Th("Usuário"), html.Th("Ação"), html.Th("Entidade"), html.Th("ID")
    ])), html.Tbody(rows)], striped=True, bordered=True, hover=True, responsive=True, className="bg-white table")
    return html.Div([table])

def gerencial_tab():
    tabs = []
    tabs.append(dbc.Tab(label="Usuários", tab_id="g_users"))
    tabs.append(dbc.Tab(label="Médicos", tab_id="g_doctors"))
    tabs.append(dbc.Tab(label="Catálogo", tab_id="g_catalog"))
    tabs.append(dbc.Tab(label="Tema", tab_id="g_theme"))
    tabs.append(dbc.Tab(label="Auditoria", tab_id="g_audit"))
    return html.Div([
        dbc.Tabs(id="g_tabs", active_tab="g_users", children=tabs, className="mb-3"),
        html.Div(id="g_content")
    ])

# --------- Modais
edit_exam_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Editar Exame")),
    dbc.ModalBody([
        dcc.Store(id="edit_exam_id"),
        dbc.Row([
            dbc.Col(dbc.Input(id="edit_exam_id_text", placeholder="ID do exame", type="text"), md=4),
            dbc.Col(dcc.Dropdown(id="edit_modalidade", options=[{"label":m,"value":m} for m in sorted(list_catalog().keys())], placeholder="Modalidade"), md=4),
            dbc.Col(dmc.Autocomplete(id="edit_exame_auto", placeholder="Exame", data=[], limit=50), md=4),
        ], className="mb-3"),
        dbc.Row([
            dbc.Col(dmc.DateTimePicker(
                id="edit_data_dt",
                placeholder="Data e hora",
                valueFormat="DD/MM/YYYY HH:mm",
                withSeconds=False
            ), md=6),
            dbc.Col(dmc.Autocomplete(id="edit_medico_auto", placeholder="Médico responsável", data=[], limit=100), md=6),
        ], className="mb-3"),
        dbc.Row([
            dbc.Col(dbc.Input(id="edit_idade", placeholder="Idade", type="number", min=0, max=120), md=3),
            dbc.Col(dbc.Checklist(id="edit_contraste_usado", options=[{"label":" Usou contraste","value":"yes"}], value=[]), md=4),
            dbc.Col(dbc.Input(id="edit_contraste_qtd", placeholder="Qtd (mL)", type="number", min=0, step=1), md=5),
        ])
    ]),
    dbc.ModalFooter([
        dbc.Button("Cancelar", id="edit_cancel", className="me-2", color="secondary"),
        dbc.Button("Salvar alterações", id="edit_save", color="primary"),
    ])
], id="edit_modal", is_open=False, size="lg", backdrop="static")

edit_user_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Editar Usuário")),
    dbc.ModalBody([
        dcc.Store(id="edit_user_id"),
        dbc.Row([
            dbc.Col(dbc.Input(id="eu_nome", placeholder="Nome completo"), md=6),
            dbc.Col(dbc.Input(id="eu_email", placeholder="E-mail", type="email"), md=6),
        ], className="mb-2"),
        dbc.Row([
            dbc.Col(dbc.Input(id="eu_senha", placeholder="(Opcional) Nova senha", type="password"), md=6),
            dbc.Col(dcc.Dropdown(id="eu_role", options=[{"label":"Usuário","value":"user"},{"label":"Administrador","value":"admin"}]), md=6),
        ], className="mb-2"),
        dbc.Checklist(id="eu_ativo", options=[{"label":" Ativo","value":"yes"}], value=["yes"]),
        html.Div(id="eu_feedback", className="mt-2")
    ]),
    dbc.ModalFooter([
        dbc.Button("Fechar", id="eu_close", className="me-2", color="secondary"),
        dbc.Button("Salvar", id="eu_save", color="primary"),
    ])
], id="eu_modal", is_open=False, backdrop="static")

pw_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Trocar senha")),
    dbc.ModalBody([
        dmc.PasswordInput(id="pw_atual", label="Senha atual", required=True),
        dmc.PasswordInput(id="pw_nova", label="Nova senha", required=True, mt=8),
        dmc.PasswordInput(id="pw_nova2", label="Confirmar nova senha", required=True, mt=8),
        html.Div(id="pw_feedback", className="mt-2")
    ]),
    dbc.ModalFooter([
        dbc.Button("Fechar", id="pw_close", className="me-2"),
        dbc.Button("Salvar", id="pw_save", color="primary")
    ])
], id="pw_modal", is_open=False, backdrop="static")

# --------- Layout (função)
def serve_layout():
    th = theme_get()
    return dbc.Container([
        theme_css_block(),
        dcc.Location(id="url"),
        user_header(),
        dbc.Tabs(id="tabs", active_tab="cadastro", children=[
            dbc.Tab(label="Cadastro", tab_id="cadastro", tab_class_name="fw-semibold"),
            dbc.Tab(label="Exames", tab_id="exames", tab_class_name="fw-semibold"),
            dbc.Tab(label="Gerencial", tab_id="gerencial", tab_class_name="fw-semibold"),
        ], className="mt-3"),
        html.Div(id="tab_content", className="mt-3"),
        edit_exam_modal,
        edit_user_modal,
        pw_modal
    ], fluid=True, className="py-2")

dash_app.layout = serve_layout

# --------------------------------------
# Callbacks - Abas principais
# --------------------------------------
@dash_app.callback(Output("tab_content","children"), Input("tabs","active_tab"))
def render_tab(tab):
    if tab == "cadastro":
        return dbc.Container([cadastro_card()], fluid=True)
    if tab == "exames":
        return exams_tab()
    if tab == "gerencial":
        return gerencial_tab()
    return html.Div()

# --------------------------------------
# Cadastro de exame
# --------------------------------------
@dash_app.callback(Output("contraste_qtd","disabled"), Input("contraste_usado","value"))
def toggle_qtd(ck): return not (ck and "yes" in ck)

@dash_app.callback(Output("edit_contraste_qtd","disabled"), Input("edit_contraste_usado","value"))
def toggle_qtd_edit(ck): return not (ck and "yes" in ck)

@dash_app.callback(Output("exame_auto","data"), Input("modalidade","value"))
def load_exames_por_modalidade(modalidade):
    cat = list_catalog()
    if not modalidade:
        todos = []
        for k, v in cat.items():
            todos += v
        return sorted(set(todos))
    return cat.get(modalidade, [])

@dash_app.callback(Output("edit_exame_auto","data"), Input("edit_modalidade","value"))
def load_exames_edit(modalidade):
    cat = list_catalog()
    if not modalidade:
        todos = []
        for k,v in cat.items():
            todos += v
        return sorted(set(todos))
    return cat.get(modalidade, [])

@dash_app.callback(Output("medico_auto","data"), Input("tabs","active_tab"))
def load_medicos_para_cadastro(tab):
    if tab != "cadastro": raise dash.exceptions.PreventUpdate
    return [d.get("nome") for d in list_doctors()]

@dash_app.callback(Output("edit_medico_auto","data"), Input("edit_modal","is_open"))
def load_medicos_para_edicao(opened):
    if not opened: raise dash.exceptions.PreventUpdate
    return [d.get("nome") for d in list_doctors()]

@dash_app.callback(
    Output("save_feedback","children"),
    Input("btn_salvar","n_clicks"),
    State("exam_id","value"), State("idade","value"), State("modalidade","value"),
    State("exame_auto","value"), State("medico_auto","value"),
    State("data_dt","value"),
    State("contraste_usado","value"), State("contraste_qtd","value"),
    prevent_initial_call=True
)
def salvar_exame(n, exam_id, idade, modalidade, exame_txt, medico, data_dt, ck, qtd):
    if not session.get("user_id"):
        return dbc.Alert("Sessão expirada. Faça login novamente.", color="warning")
    miss=[]
    if not exam_id: miss.append("ID do exame")
    if idade is None: miss.append("Idade")
    if not modalidade: miss.append("Modalidade")
    if not exame_txt: miss.append("Exame")
    if not medico: miss.append("Médico")
    if not data_dt: miss.append("Data/Hora")
    if miss: return dbc.Alert(f"Campos obrigatórios: {', '.join(miss)}", color="danger")
    try:
        dt = datetime.fromisoformat(data_dt)
    except Exception as e:
        return dbc.Alert(f"Data/Hora inválida: {e}.", color="danger")
    contraste = bool(ck and "yes" in ck); qtd = float(qtd or 0)
    if not contraste: qtd=0
    u=current_user()
    rec={"exam_id":str(exam_id).strip(),"idade":int(idade),"modalidade":str(modalidade),"exame":str(exame_txt).strip(),
         "medico":str(medico).strip(),"data_hora":dt.replace(microsecond=0).isoformat(),
         "contraste_usado":contraste,"contraste_qtd":qtd,
         "user_email":u.get("email") if u else None}
    new_id = add_exam(rec)
    log_action(u.get("email") if u else None, "create", "exam", new_id, before=None, after=rec)
    return dbc.Alert("Exame salvo com sucesso!", color="success", duration=4000)

# --------------------------------------
# Lista de exames (editar/excluir)
# --------------------------------------
@dash_app.callback(
    Output("exams_feedback","children", allow_duplicate=True),
    Output("exams_table","children", allow_duplicate=True),
    Input({"type":"del_btn","id":ALL},"n_clicks"),
    prevent_initial_call=True
)
def excluir_exame(n_clicks):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    btn = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        btn_id = ast.literal_eval(btn)
        exam_id = int(btn_id["id"])
    except Exception:
        raise dash.exceptions.PreventUpdate
    before = next((x for x in list_exams() if x.get("id")==exam_id), None)
    ok = delete_exam(exam_id)
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    if ok:
        log_action(session.get("user_email"), "delete", "exam", exam_id, before=before, after=None)
        return dbc.Alert(f"Exame {exam_id} excluído.", color="success", duration=3000), exams_table_component(rows)
    return dbc.Alert("Nada excluído.", color="secondary", duration=3000), exams_table_component(rows)

@dash_app.callback(
    Output("edit_modal","is_open"),
    Output("edit_exam_id","data"),
    Output("edit_exam_id_text","value"),
    Output("edit_modalidade","value"),
    Output("edit_exame_auto","value"),
    Output("edit_data_dt","value"),
    Output("edit_medico_auto","value"),
    Output("edit_idade","value"),
    Output("edit_contraste_usado","value"),
    Output("edit_contraste_qtd","value"),
    Input({"type":"edit_btn","id":ALL},"n_clicks"),
    Input("edit_cancel","n_clicks"),
    State("edit_modal","is_open"),
    prevent_initial_call=True
)
def open_edit_modal(edit_clicks, cancel_click, is_open):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    prop = ctx.triggered[0]["prop_id"]
    if prop == "edit_cancel.n_clicks":
        return False, None, None, None, None, None, None, None, [], None
    if not any([c for c in (edit_clicks or []) if c]):
        raise dash.exceptions.PreventUpdate
    trig = prop.split(".")[0]
    try:
        trig_id = ast.literal_eval(trig)
        exam_id = int(trig_id["id"])
    except Exception:
        raise dash.exceptions.PreventUpdate
    e = next((x for x in list_exams() if x.get("id")==exam_id), None)
    if not e: raise dash.exceptions.PreventUpdate
    e_dt_value = None
    try:
        dt = datetime.fromisoformat(e.get("data_hora")); e_dt_value = dt.replace(microsecond=0).isoformat()
    except: pass
    ck = ["yes"] if e.get("contraste_usado") else []
    return True, exam_id, e.get("exam_id"), e.get("modalidade"), e.get("exame"), e_dt_value, e.get("medico"), e.get("idade"), ck, e.get("contraste_qtd")

@dash_app.callback(
    Output("edit_modal","is_open", allow_duplicate=True),
    Output("exams_feedback","children", allow_duplicate=True),
    Output("exams_table","children", allow_duplicate=True),
    Input("edit_save","n_clicks"),
    State("edit_exam_id","data"),
    State("edit_exam_id_text","value"),
    State("edit_modalidade","value"),
    State("edit_exame_auto","value"),
    State("edit_data_dt","value"),
    State("edit_medico_auto","value"),
    State("edit_idade","value"),
    State("edit_contraste_usado","value"),
    State("edit_contraste_qtd","value"),
    prevent_initial_call=True
)
def save_edit(n, exam_id, exam_id_text, modalidade, exame_txt, edit_data_dt, medico, idade, ck, qtd):
    if not exam_id: raise dash.exceptions.PreventUpdate
    miss=[]
    if not exam_id_text: miss.append("ID do exame")
    if idade is None: miss.append("Idade")
    if not modalidade: miss.append("Modalidade")
    if not exame_txt: miss.append("Exame")
    if not medico: miss.append("Médico")
    if not edit_data_dt: miss.append("Data/Hora")
    if miss: return True, dbc.Alert(f"Campos obrigatórios: {', '.join(miss)}", color="danger"), no_update
    try:
        dt = datetime.fromisoformat(edit_data_dt)
    except Exception as e:
        return True, dbc.Alert(f"Data/Hora inválida: {e}.", color="danger"), no_update
    contraste = bool(ck and "yes" in ck); qtd=float(qtd or 0)
    if not contraste: qtd=0
    before = next((x for x in list_exams() if x.get("id")==int(exam_id)), None)
    changed = update_exam(int(exam_id), {
        "exam_id":str(exam_id_text).strip(),"modalidade":str(modalidade),
        "exame":str(exame_txt).strip(),"medico":str(medico).strip(),
        "data_hora":dt.replace(microsecond=0).isoformat(),"idade":int(idade),
        "contraste_usado":contraste,"contraste_qtd":qtd
    })
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    if changed:
        after = next((x for x in rows if x.get("id")==int(exam_id)), None)
        log_action(session.get("user_email"), "update", "exam", int(exam_id), before=before, after=after)
        return False, dbc.Alert("Exame atualizado com sucesso!", color="success", duration=3000), exams_table_component(rows)
    else:
        return True, dbc.Alert("Nenhuma alteração aplicada.", color="secondary", duration=3000), exams_table_component(rows)

# --------------------------------------
# Gerencial - conteúdo por sub-aba
# --------------------------------------
@dash_app.callback(Output("g_content","children"), Input("g_tabs","active_tab"))
def render_g_tab(tid):
    if tid=="g_users":   return gerencial_users()
    if tid=="g_doctors": return gerencial_doctors()
    if tid=="g_catalog": return gerencial_catalog()
    if tid=="g_theme":   return gerencial_theme()
    if tid=="g_audit":   return gerencial_logs()
    return html.Div()

# ---- Usuários (admin)
@dash_app.callback(
    Output("nu_feedback","children"),
    Output("users_feedback","children", allow_duplicate=True),
    Input("nu_criar","n_clicks"),
    State("nu_nome","value"), State("nu_email","value"),
    State("nu_senha","value"), State("nu_role","value"),
    State("nu_ativo","value"),
    prevent_initial_call=True
)
def create_user(n, nome, email, senha, role, ativo_val):
    if not is_admin(): return dmc.Alert("Sem permissão.", color="red"), no_update
    nome=(nome or "").strip(); email=(email or "").strip().lower(); senha=(senha or "").strip()
    role=role or "user"; ativo = bool(ativo_val and "yes" in ativo_val)
    miss=[]
    if not nome: miss.append("Nome")
    if not email: miss.append("E-mail")
    if not senha: miss.append("Senha")
    if miss: return dmc.Alert(f"Campos obrigatórios: {', '.join(miss)}", color="red"), no_update
    if get_user_by_email(email):
        return dmc.Alert("E-mail já cadastrado.", color="red"), no_update
    uid = add_user(nome, email, senha, role=role, ativo=ativo)
    log_action(session.get("user_email"), "create", "user", uid, before=None, after={"id":uid,"email":email})
    return dmc.Alert("Usuário criado.", color="green"), no_update

@dash_app.callback(
    Output("eu_modal","is_open"),
    Output("edit_user_id","data"),
    Output("eu_nome","value"),
    Output("eu_email","value"),
    Output("eu_role","value"),
    Output("eu_ativo","value"),
    Input({"type":"edit_user_btn","id":ALL},"n_clicks"),
    Input("eu_close","n_clicks"),
    State("eu_modal","is_open"),
    prevent_initial_call=True
)
def open_edit_user(n_edit, n_close, is_open):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    trig = ctx.triggered[0]["prop_id"]
    if trig=="eu_close.n_clicks":
        return False, None, None, None, None, []
    # qual usuário?
    btn = trig.split(".")[0]
    try:
        b = ast.literal_eval(btn); uid = int(b["id"])
    except:
        raise dash.exceptions.PreventUpdate
    u = next((x for x in list_users() if x.get("id")==uid), None)
    if not u: raise dash.exceptions.PreventUpdate
    return True, uid, u.get("nome"), u.get("email"), u.get("role"), (["yes"] if u.get("ativo") else [])

@dash_app.callback(
    Output("eu_modal","is_open", allow_duplicate=True),
    Output("users_feedback","children", allow_duplicate=True),
    Input("eu_save","n_clicks"),
    State("edit_user_id","data"),
    State("eu_nome","value"), State("eu_email","value"),
    State("eu_senha","value"), State("eu_role","value"),
    State("eu_ativo","value"),
    prevent_initial_call=True
)
def save_user_edit(n, uid, nome, email, senha, role, ativo_val):
    if not is_admin(): raise dash.exceptions.PreventUpdate
    if not uid: raise dash.exceptions.PreventUpdate
    users = list_users()
    before = next((x for x in users if x.get("id")==uid), None)
    patch = {}
    if nome: patch["nome"] = nome.strip()
    if email: patch["email"] = email.strip().lower()
    if role: patch["role"] = role
    patch["ativo"] = bool(ativo_val and "yes" in ativo_val)
    if (senha or "").strip():
        patch["senha"] = senha.strip()
    update_user(uid, patch)
    after = next((x for x in list_users() if x.get("id")==uid), None)
    log_action(session.get("user_email"), "update", "user", uid, before=before, after=after)
    return False, dmc.Alert("Usuário atualizado.", color="green")

@dash_app.callback(
    Output("users_feedback","children", allow_duplicate=True),
    Input({"type":"toggle_user_btn","id":ALL},"n_clicks"),
    prevent_initial_call=True
)
def toggle_user(n_clicks):
    if not is_admin(): raise dash.exceptions.PreventUpdate
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    btn = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        b = ast.literal_eval(btn); uid = int(b["id"])
    except:
        raise dash.exceptions.PreventUpdate
    users = list_users()
    u = next((x for x in users if x.get("id")==uid), None)
    if not u: raise dash.exceptions.PreventUpdate
    before = dict(u)
    u["ativo"] = not u.get("ativo", True)
    save_users(users)
    log_action(session.get("user_email"), "update", "user_toggle", uid, before=before, after=u)
    return dmc.Alert(f"Usuário {'ativado' if u['ativo'] else 'desativado'}.", color="green")

# ---- Médicos
@dash_app.callback(
    Output("docs_feedback","children"),
    Output("docs_list","children"),
    Output("medico_auto","data", allow_duplicate=True),
    Input("btn_add_medico","n_clicks"),
    State("novo_medico","value"),
    prevent_initial_call=True
)
def add_medico(n, nome):
    nome = (nome or "").strip()
    if not nome:
        return dbc.Alert("Informe o nome do médico.", color="warning"), no_update, no_update
    new_id = add_doctor(nome)
    log_action(session.get("user_email"), "create", "doctor", new_id, before=None, after={"id":new_id,"nome":nome})
    docs = list_doctors()
    list_items = [html.Li([html.Span(d.get("nome")),
                           dbc.Button("Excluir", id={"type":"del_doc_btn","id":d.get("id")},
                                      size="sm", color="danger", outline=True, className="ms-2")]) for d in docs]
    return dbc.Alert("Médico adicionado!", color="success", duration=2500), list_items, [d.get("nome") for d in docs]

@dash_app.callback(
    Output("docs_feedback","children", allow_duplicate=True),
    Output("docs_list","children", allow_duplicate=True),
    Output("medico_auto","data", allow_duplicate=True),
    Input({"type":"del_doc_btn","id":ALL},"n_clicks"),
    prevent_initial_call=True
)
def del_medico(n_clicks):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    btn = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        b = ast.literal_eval(btn); did = int(b["id"])
    except:
        raise dash.exceptions.PreventUpdate
    # evitar excluir se médico estiver em uso (opcional simples)
    if any(e.get("medico") in [d.get("nome") for d in list_doctors() if d.get("id")==did] for e in list_exams()):
        # ainda assim excluir, mas ideal seria checar antes. Mantendo simples:
        pass
    before = next((d for d in list_doctors() if d.get("id")==did), None)
    ok = delete_doctor(did)
    docs = list_doctors()
    list_items = [html.Li([html.Span(d.get("nome")),
                           dbc.Button("Excluir", id={"type":"del_doc_btn","id":d.get("id")},
                                      size="sm", color="danger", outline=True, className="ms-2")]) for d in docs]
    if ok:
        log_action(session.get("user_email"), "delete", "doctor", did, before=before, after=None)
        return dbc.Alert("Médico excluído.", color="success", duration=2500), list_items, [d.get("nome") for d in docs]
    return dbc.Alert("Nada excluído.", color="secondary", duration=2500), list_items, [d.get("nome") for d in docs]

# ---- Catálogo
@dash_app.callback(
    Output("cat_feedback","children"),
    Output("cat_lista","children"),
    Input("cat_criar_mod","n_clicks"),
    State("cat_nova_modalidade","value"),
    prevent_initial_call=True
)
def criar_modalidade(n, nova):
    nova=(nova or "").strip()
    if not nova: return dmc.Alert("Informe o nome da modalidade.", color="red"), no_update
    cat = list_catalog()
    if nova in cat: return dmc.Alert("Modalidade já existe.", color="yellow"), no_update
    cat[nova] = []
    save_catalog(cat)
    log_action(session.get("user_email"), "create", "catalog_mod", nova)
    return dmc.Alert("Modalidade criada.", color="green"), cat_list_component(cat, selected=nova)

@dash_app.callback(
    Output("cat_feedback","children", allow_duplicate=True),
    Output("cat_lista","children", allow_duplicate=True),
    Input("cat_add_exame","n_clicks"),
    State("cat_modalidade","value"),
    State("cat_exame_nome","value"),
    prevent_initial_call=True
)
def add_exame_catalogo(n, mod, nome):
    mod=(mod or "").strip(); nome=(nome or "").strip()
    if not mod or not nome:
        return dmc.Alert("Selecione a modalidade e informe o exame.", color="red"), no_update
    cat = list_catalog()
    cat.setdefault(mod, [])
    if nome in cat[mod]:
        return dmc.Alert("Esse exame já existe nessa modalidade.", color="yellow"), no_update
    cat[mod].append(nome)
    save_catalog(cat)
    log_action(session.get("user_email"), "create", "catalog_exam", f"{mod}:{nome}")
    return dmc.Alert("Exame adicionado.", color="green"), cat_list_component(cat, selected=mod)

@dash_app.callback(
    Output("cat_feedback","children", allow_duplicate=True),
    Output("cat_lista","children", allow_duplicate=True),
    Input({"type":"cat_del_ex","mod":ALL,"name":ALL},"n_clicks"),
    prevent_initial_call=True
)
def del_exame_catalogo(n_clicks):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    btn = ctx.triggered[0]["prop_id"].split(".")[0]
    try:
        b = ast.literal_eval(btn); mod = b["mod"]; nome = b["name"]
    except:
        raise dash.exceptions.PreventUpdate
    cat = list_catalog()
    before = list(cat.get(mod, []))
    if mod in cat and nome in cat[mod]:
        cat[mod].remove(nome)
        save_catalog(cat)
        log_action(session.get("user_email"), "delete", "catalog_exam", f"{mod}:{nome}", before=before, after=cat.get(mod))
        return dmc.Alert("Exame removido.", color="green"), cat_list_component(cat, selected=mod)
    return dmc.Alert("Nada removido.", color="yellow"), cat_list_component(cat, selected=mod)

# ---- Tema (upload logo + nome + escolha de estilo)
@dash_app.callback(
    Output("theme_feedback","children"),
    Output("theme_logo_preview","children"),
    Input("theme_logo_upload","contents"),
    State("theme_logo_upload","filename"),
    prevent_initial_call=True
)
def upload_logo(contents, filename):
    if not contents:
        raise dash.exceptions.PreventUpdate
    try:
        header, b64 = contents.split(",", 1)
        data = base64.b64decode(b64)
        ext = ".png"
        if filename and "." in filename:
            ext = "." + filename.split(".")[-1].lower()[:4]
            if ext not in [".png",".jpg",".jpeg",".webp"]: ext=".png"
        fname = f"logo_{uuid.uuid4().hex}{ext}"
        fpath = UPLOAD_DIR / fname
        with open(fpath, "wb") as f:
            f.write(data)
        theme_set({"logo_path": f"uploads/{fname}"})
        return dmc.Alert("Logo atualizado!", color="green"), html.Img(src=f"/uploads/{fname}", style={"maxHeight":"80px"})
    except Exception as e:
        return dmc.Alert(f"Falha no upload: {e}", color="red"), no_update

@dash_app.callback(
    Output("theme_feedback","children", allow_duplicate=True),
    Input("theme_save","n_clicks"),
    State("theme_portal_name","value"),
    State("theme_choice","value"),
    prevent_initial_call=True
)
def save_theme(n, name, choice):
    name=(name or "").strip()
    if not name: return dmc.Alert("Informe o nome do portal.", color="red")
    t = theme_set({"portal_name": name, "theme": choice or "light"})
    return dmc.Alert("Tema salvo. Recarregue a página para aplicar totalmente.", color="green")

# ---- Auditoria não precisa de callbacks (lista simples)

# ---- Trocar senha
@dash_app.callback(Output("pw_modal","is_open", allow_duplicate=True),
                   Input("open_pw_modal","n_clicks"),
                   State("pw_modal","is_open"),
                   prevent_initial_call=True)
def open_pw(n, is_open): return not is_open

@dash_app.callback(
    Output("pw_modal","is_open", allow_duplicate=True),
    Output("pw_feedback","children"),
    Input("pw_save","n_clicks"),
    Input("pw_close","n_clicks"),
    State("pw_atual","value"),
    State("pw_nova","value"),
    State("pw_nova2","value"),
    prevent_initial_call=True
)
def do_change_pw(ns, nc, atual, nova, nova2):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    trig = ctx.triggered[0]["prop_id"]
    if trig == "pw_close.n_clicks":
        return False, ""
    u = current_user()
    if not u:
        return True, dmc.Alert("Sessão expirada.", color="red")
    if (u.get("senha") != (atual or "")):
        return True, dmc.Alert("Senha atual incorreta.", color="red")
    if not nova or nova != nova2:
        return True, dmc.Alert("Confirmação não confere.", color="red")
    users = list_users()
    for x in users:
        if x.get("id")==u["id"]:
            x["senha"]=nova
            break
    save_users(users)
    log_action(u.get("email"), "update", "user_pw", u["id"])
    return False, dmc.Alert("Senha alterada com sucesso!", color="green")

# --------------------------------------
# Start
# --------------------------------------
if __name__=="__main__":
    dash_app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8050")),
        debug=os.getenv("DEBUG", "False").lower()=="true"
    )
