# VENV
# /home/ubuntu/.venv
# cd /home/ubuntu/
# source .venv/bin/activate

# SERVICES
# sudo systemctl daemon-reload
# sudo systemctl restart portal-radiologico
# sudo systemctl status portal-radiologico --no-pager -l

# app.py
import os, json, uuid, ast
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps

from flask import Flask, request, session, redirect, url_for, send_from_directory, jsonify, render_template_string

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

USERS_JSON = DATA_DIR / "users.json"
DOCTORS_JSON = DATA_DIR / "doctors.json"
EXAMS_JSON = DATA_DIR / "exams.json"
CATALOG_JSON = DATA_DIR / "exam_catalog.json"
THEME_JSON = DATA_DIR / "theme.json"
AUDIT_JSON = DATA_DIR / "audit_log.json"

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

# --------------------------------------
# Seed inicial (se vazio)
# --------------------------------------
def seed_if_needed():
    if not USERS_JSON.exists():
        users = [
            {"id": 1, "nome": "Administrador", "email": "admin@local", "senha": "admin123", "ativo": True, "role": "admin"}
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
            "theme": "light"
        }
        save_json(THEME_JSON, theme)
    if not AUDIT_JSON.exists():
        save_json(AUDIT_JSON, [])

seed_if_needed()

# --------------------------------------
# Helpers de domínio
# --------------------------------------
def list_users():
    return load_json(USERS_JSON, [])

def get_user_by_email(email):
    return next((u for u in list_users() if u.get("email")==email), None)

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return next((u for u in list_users() if u.get("id")==uid), None)

def list_doctors():
    return load_json(DOCTORS_JSON, [])

def add_doctor(nome: str):
    docs = list_doctors()
    new_id = (max([d["id"] for d in docs]) + 1) if docs else 1
    docs.append({"id": new_id, "nome": nome})
    save_json(DOCTORS_JSON, docs)
    return new_id

def list_catalog():
    return load_json(CATALOG_JSON, {})

def list_exams():
    return load_json(EXAMS_JSON, [])

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
    logs = load_json(AUDIT_JSON, [])
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
    # protege tudo que começa com /app/
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

def theme_get():
    return load_json(THEME_JSON, {"portal_name":"Portal Radiológico","logo_path":None,"theme":"light"})

def theme_logo_url():
    theme = theme_get()
    lp = theme.get("logo_path")
    if lp:
        # se já está com prefixo uploads/...
        if lp.startswith("uploads/"):
            return f"/{lp}"
        return f"/uploads/{lp}"
    return None

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
    theme = theme_get()
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
        portal_name = theme.get("portal_name") or "Portal Radiológico",
        logo_url = theme_logo_url(),
        theme_url = "https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css",
        error = error
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

# --------- UI helpers
MODALIDADES = list(list_catalog().keys())

def mod_label(m):
    return m

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
            # só indicação do usuário à direita + menu
            html.Div(),
            dbc.DropdownMenu(
                label=f"{name}",
                children=[
                    dbc.DropdownMenuItem(email, disabled=True),
                    dbc.DropdownMenuItem(divider=True),
                    dbc.DropdownMenuItem("Trocar senha", id="open_pw_modal"),
                    dbc.DropdownMenuItem("Sair", href="/logout", id="logout_btn"),
                ],
                color="secondary",
                className="ms-auto"
            )
        ], fluid=True),
        color="light", className="border-bottom shadow-sm", sticky="top"
    )

def cadastro_card():
    return dbc.Card([
        dbc.CardHeader("Cadastro de Exame (Atendimento)", className="fw-semibold"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(dbc.Input(id="exam_id", placeholder="ID do exame", type="text"), md=3),
                dbc.Col(dcc.Dropdown(id="modalidade", options=[{"label":mod_label(m),"value":m} for m in MODALIDADES], placeholder="Modalidade"), md=3),
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
                    data=[],  # preenchido por callback
                    limit=100
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
    ], className="shadow-sm")

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
    return dbc.Table([header, html.Tbody(body)], striped=True, bordered=True, hover=True, responsive=True, className="bg-white")

def exams_tab():
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    return dbc.Container([
        html.H4("Exames cadastrados"),
        html.Div(id="exams_feedback"),
        html.Div(id="exams_table", children=exams_table_component(rows)),
    ], fluid=True)

def gerencial_tab():
    # Só demonstrativo simples: lista médicos e catálogo
    docs = list_doctors()
    cat = list_catalog()
    return dbc.Container([
        html.H4("Gerencial"),
        dbc.Row([
            dbc.Col([
                html.H5("Médicos"),
                dmc.TextInput(id="novo_medico", placeholder="Nome do médico"),
                dmc.Button("Adicionar", id="btn_add_medico", mt=10),
                html.Div(id="docs_feedback", className="mt-2"),
                html.Ul([html.Li(d.get("nome")) for d in docs], id="docs_list", className="mt-3")
            ], md=6),
            dbc.Col([
                html.H5("Catálogo por Modalidade"),
                html.Div([
                    html.P([html.Strong(m), ": ", ", ".join(v)]) for m, v in cat.items()
                ], className="small")
            ], md=6)
        ])
    ], fluid=True)

# Modal de edição de exame
edit_modal = dbc.Modal([
    dbc.ModalHeader(dbc.ModalTitle("Editar Exame")),
    dbc.ModalBody([
        dcc.Store(id="edit_exam_id"),
        dbc.Row([
            dbc.Col(dbc.Input(id="edit_exam_id_text", placeholder="ID do exame", type="text"), md=4),
            dbc.Col(dcc.Dropdown(id="edit_modalidade", options=[{"label":m,"value":m} for m in MODALIDADES], placeholder="Modalidade"), md=4),
            dbc.Col(dmc.Autocomplete(id="edit_exame_auto", placeholder="Exame", data=[], limit=50), md=4),
        ], className="mb-3"),
        dbc.Row([
            dbc.Col(dmc.DateTimePicker(
                id="edit_data_dt",
                placeholder="Data e hora",
                valueFormat="DD/MM/YYYY HH:mm",
                withSeconds=False
            ), md=6),
            dbc.Col(dmc.Autocomplete(
                id="edit_medico_auto",
                placeholder="Médico responsável",
                data=[], limit=100
            ), md=6),
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

# Modal de trocar senha
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

dash_app.layout = dbc.Container([
    dcc.Location(id="url"),
    user_header(),
    html.Div(id="gate", className="d-none"),  # placeholder para auth messages
    dbc.Tabs(id="tabs", active_tab="cadastro", children=[
        dbc.Tab(label="Cadastro", tab_id="cadastro", tab_class_name="fw-semibold"),
        dbc.Tab(label="Exames", tab_id="exames", tab_class_name="fw-semibold"),
        dbc.Tab(label="Gerencial", tab_id="gerencial", tab_class_name="fw-semibold"),
    ], className="mt-3"),
    html.Div(id="tab_content", className="mt-3"),
    edit_modal,
    pw_modal
], fluid=True, className="py-2")

# --------------------------------------
# Callbacks
# --------------------------------------
# Conteúdo das abas
@dash_app.callback(Output("tab_content","children"), Input("tabs","active_tab"))
def render_tab(tab):
    if tab == "cadastro":
        return dbc.Container([cadastro_card()], fluid=True)
    if tab == "exames":
        return exams_tab()
    if tab == "gerencial":
        return gerencial_tab()
    return html.Div()

# Habilitar/Desabilitar quantidade de contraste
@dash_app.callback(Output("contraste_qtd","disabled"), Input("contraste_usado","value"))
def toggle_qtd(ck):
    return not (ck and "yes" in ck)

@dash_app.callback(Output("edit_contraste_qtd","disabled"), Input("edit_contraste_usado","value"))
def toggle_qtd_edit(ck):
    return not (ck and "yes" in ck)

# Popular exames conforme modalidade
@dash_app.callback(Output("exame_auto","data"), Input("modalidade","value"))
def load_exames_por_modalidade(modalidade):
    cat = list_catalog()
    if not modalidade:
        # todas opções
        todos = []
        for k, v in cat.items():
            todos += v
        return sorted(list(set(todos)))
    return cat.get(modalidade, [])

@dash_app.callback(Output("edit_exame_auto","data"), Input("edit_modalidade","value"))
def load_exames_edit(modalidade):
    cat = list_catalog()
    if not modalidade:
        todos = []
        for k,v in cat.items():
            todos += v
        return sorted(list(set(todos)))
    return cat.get(modalidade, [])

# >>> Popular médicos (cadastro) quando abre a aba
@dash_app.callback(Output("medico_auto","data"), Input("tabs","active_tab"))
def load_medicos_para_cadastro(tab):
    if tab != "cadastro":
        raise dash.exceptions.PreventUpdate
    return [d.get("nome") for d in list_doctors()]

# >>> Popular médicos (edição) quando abre o modal
@dash_app.callback(Output("edit_medico_auto","data"), Input("edit_modal","is_open"))
def load_medicos_para_edicao(opened):
    if not opened:
        raise dash.exceptions.PreventUpdate
    return [d.get("nome") for d in list_doctors()]

# Salvar exame novo
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
    try:
        new_id = add_exam(rec)
        log_action(u.get("email") if u else None, "create", "exam", new_id, before=None, after=rec)
        return dbc.Alert("Exame salvo com sucesso!", color="success", duration=4000)
    except Exception as e:
        return dbc.Alert(f"Erro ao salvar: {e}", color="danger")

# Tabela: clique Excluir
@dash_app.callback(
    Output("exams_feedback","children", allow_duplicate=True),
    Output("exams_table","children", allow_duplicate=True),
    Input({"type":"del_btn","id":ALL},"n_clicks"),
    prevent_initial_call=True
)
def excluir_exame(n_clicks):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    # identifica qual botão
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

# Abrir modal de edição
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

# Salvar edição
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

# Gerencial: adicionar médico
@dash_app.callback(
    Output("docs_feedback","children"),
    Output("docs_list","children"),
    Input("btn_add_medico","n_clicks"),
    State("novo_medico","value"),
    prevent_initial_call=True
)
def add_medico(n, nome):
    nome = (nome or "").strip()
    if not nome:
        return dbc.Alert("Informe o nome do médico.", color="warning"), no_update
    new_id = add_doctor(nome)
    log_action(session.get("user_email"), "create", "doctor", new_id, before=None, after={"id":new_id,"nome":nome})
    docs = list_doctors()
    return dbc.Alert("Médico adicionado!", color="success", duration=2500), [html.Li(d.get("nome")) for d in docs]

# Abrir/fechar modal trocar senha
@dash_app.callback(Output("pw_modal","is_open", allow_duplicate=True), Input("open_pw_modal","n_clicks"), State("pw_modal","is_open"), prevent_initial_call=True)
def open_pw(n, is_open):
    return not is_open

@dash_app.callback(Output("pw_modal","is_open", allow_duplicate=True), Output("pw_feedback","children"),
                  Input("pw_save","n_clicks"), Input("pw_close","n_clicks"),
                  State("pw_atual","value"), State("pw_nova","value"), State("pw_nova2","value"),
                  prevent_initial_call=True)
def do_change_pw(ns, nc, atual, nova, nova2):
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    trig = ctx.triggered[0]["prop_id"]
    if trig=="pw_close.n_clicks":
        return False, ""
    u = current_user()
    if not u:
        return True, dmc.Alert("Sessão expirada.", color="red")
    if (u.get("senha")!= (atual or "")):
        return True, dmc.Alert("Senha atual incorreta.", color="red")
    if not nova or nova!=nova2:
        return True, dmc.Alert("Confirmação não confere.", color="red")
    users = list_users()
    for x in users:
        if x["id"]==u["id"]:
            x["senha"]=nova
            break
    save_json(USERS_JSON, users)
    log_action(u.get("email"), "update", "user_pw", u["id"])
    return False, dmc.Alert("Senha alterada com sucesso!", color="green")

# --------------------------------------
# Start
# --------------------------------------
# Importante para produção via systemd ou python app.py
# (Dash já está montado em /app/)
# -------------------- Start --------------------
if __name__=="__main__":
    dash_app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8050")),
        debug=os.getenv("DEBUG", "False").lower()=="true"
    )
