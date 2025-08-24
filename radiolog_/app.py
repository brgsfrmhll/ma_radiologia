# VENV
# /home/ubuntu/.venv
# cd /home/ubuntu/
# source .venv/bin/activate

# SERVICES
# sudo systemctl daemon-reload
# sudo systemctl restart portal-radiologico
# sudo systemctl status portal-radiologico --no-pager -l

# app.py
# Portal Radiológico - Local (JSON)
# - Customização (admin): nome do portal, tema (Bootswatch) com preview, logo (aparece no login)
# - Cabeçalho: título do portal centralizado + menu do usuário (trocar senha & logout)
# - Abas centralizadas (Cadastro, Dashboard, Exames, Gerencial, Exportar)
# - DateTimePicker (Dash Mantine) com locale pt-BR
# - Cadastro de EXAMES (atendimentos) com Autocomplete de Catálogo por modalidade
# - Menu GERENCIAL: Usuários, Médicos, Catálogo de Exames, Logs, Customização
# - Lista/Editar/Excluir com CONFIRMAÇÃO e LOG de auditoria
# - Semeado: catálogo de exames + alguns exames iniciais (editáveis)

import os, json, threading, ast, base64
from datetime import datetime, timedelta
from functools import wraps
import re # Adicionado para validação de email

import pandas as pd
from flask import Flask, request, redirect, url_for, session, render_template_string, make_response, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

import dash
from dash import html, dcc, Input, Output, State, ALL, no_update
import dash_bootstrap_components as dbc
import dash_mantine_components as dmc
import plotly.express as px

# -------------------- Configurações --------------------
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-prod")
SESSION_TIMEOUT_MIN = int(os.getenv("SESSION_TIMEOUT_MIN", "30"))
DATA_DIR = os.getenv("DATA_DIR", "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")

USERS_FILE = os.getenv("USERS_FILE", os.path.join(DATA_DIR, "users.json"))
EXAMS_FILE = os.getenv("EXAMS_FILE", os.path.join(DATA_DIR, "exams.json"))           # atendimentos realizados
DOCTORS_FILE = os.getenv("DOCTORS_FILE", os.path.join(DATA_DIR, "doctors.json"))
EXAMTYPES_FILE = os.getenv("EXAMTYPES_FILE", os.path.join(DATA_DIR, "exam_types.json"))  # catálogo
LOGS_FILE = os.getenv("LOGS_FILE", os.path.join(DATA_DIR, "logs.json"))
SETTINGS_FILE = os.getenv("SETTINGS_FILE", os.path.join(DATA_DIR, "settings.json"))

# Locks para acesso seguro aos arquivos JSON em ambiente multi-threaded
_users_lock, _exams_lock, _doctors_lock, _examtypes_lock, _logs_lock, _settings_lock = (
    threading.Lock(), threading.Lock(), threading.Lock(), threading.Lock(), threading.Lock(), threading.Lock()
)

# Mapeamento de temas (Bootswatch) -> CDN CSS
THEMES = {
    "Flatly":  "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/flatly/bootstrap.min.css",
    "Lux":     "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/lux/bootstrap.min.css",
    "Materia": "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/materia/bootstrap.min.css",
    "Yeti":    "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/yeti/bootstrap.min.css",
    "Morph":   "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/morph/bootstrap.min.css",
    "Quartz":  "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/quartz/bootstrap.min.css",
    "Cyborg (escuro)": "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/cyborg/bootstrap.min.css",
}
DEFAULT_SETTINGS = {
    "portal_name": "Portal Radiológico",
    "theme": "Flatly",
    "logo_file": None,   # ex.: "logo.png"
    "logo_height_px": 40 # ## MODIFICAÇÃO: Altura padrão do logo na tela de login
}
MODALIDADES = ["RX","CT","US","MR","MG","NM"]
MOD_LABEL = {"RX":"Raio-X", "CT":"Tomografia", "US":"Ultrassom", "MR":"Ressonância", "MG":"Mamografia", "NM":"Medicina Nuclear"}
def mod_label(m): return MOD_LABEL.get(m, m or "")

# -------------------- Helpers para manipulação de JSON --------------------
def ensure_dirs():
    """Garante que os diretórios de dados e uploads existam."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

def read_json(path, default):
    """Lê um arquivo JSON, retornando o default em caso de erro ou arquivo inexistente."""
    if not os.path.exists(path): return default
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except json.JSONDecodeError: # Mais específico para erros de JSON
        print(f"Erro ao decodificar JSON em {path}. Usando valor padrão.")
        return default
    except Exception as e:
        print(f"Erro inesperado ao ler {path}: {e}. Usando valor padrão.")
        return default

def write_json(path, data, lock):
    """Escreve dados em um arquivo JSON de forma segura, usando um arquivo temporário e um lock."""
    tmp = path + ".tmp"
    with lock:
        try:
            with open(tmp,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
            os.replace(tmp,path)
        except Exception as e:
            print(f"Erro ao escrever JSON em {path}: {e}")

def read_settings():
    """Lê as configurações do portal, garantindo um tema válido."""
    s = read_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy())
    if s.get("theme") not in THEMES: s["theme"] = "Flatly"
    # ## MODIFICAÇÃO: Garante que logo_height_px exista
    if "logo_height_px" not in s:
        s["logo_height_px"] = DEFAULT_SETTINGS["logo_height_px"]
    return s

def write_settings(s):
    """Atualiza e persiste as configurações do portal."""
    cur = read_settings()
    cur.update(s or {})
    write_json(SETTINGS_FILE, cur, _settings_lock)
    return cur

SEED_USER = {
    "nome":"Administrador","email":"admin@local",
    "senha_hash": generate_password_hash("admin123"),
    "modalidades_permitidas":"*","perfil":"admin","id":1
}

def init_files():
    """Inicializa os arquivos de dados se não existirem, semeando dados iniciais."""
    ensure_dirs()
    # Usuários
    users = read_json(USERS_FILE, {"users":[]})
    if not users["users"]:
        users["users"] = [SEED_USER]; write_json(USERS_FILE, users, _users_lock)

    # Catálogo de tipos de exame
    et = read_json(EXAMTYPES_FILE, {"exam_types":[]})
    if not et["exam_types"]:
        seed_types = [
            {"id":1, "modalidade":"RX", "nome":"Tórax PA/L", "codigo":"RX001"},
            {"id":2, "modalidade":"RX", "nome":"Coluna Lombar AP/L", "codigo":"RX002"},
            {"id":3, "modalidade":"CT", "nome":"Crânio", "codigo":"CT001"},
            {"id":4, "modalidade":"CT", "nome":"Abdômen", "codigo":"CT002"},
            {"id":5, "modalidade":"CT", "nome":"Tórax", "codigo":"CT003"},
            {"id":6, "modalidade":"CT", "nome":"Coluna Lombar", "codigo":"CT004"},
            {"id":7, "modalidade":"US", "nome":"Abdômen total", "codigo":"US001"},
            {"id":8, "modalidade":"US", "nome":"Pélvico", "codigo":"US002"},
            {"id":9,  "modalidade":"MR", "nome":"Crânio", "codigo":"MR001"},
            {"id":10, "modalidade":"MR", "nome":"Joelho", "codigo":"MR002"},
            {"id":11, "modalidade":"MR", "nome":"Coluna Lombo-sacra", "codigo":"MR003"},
            {"id":12, "modalidade":"MG", "nome":"Mamografia Bilateral", "codigo":"MG001"},
            {"id":13, "modalidade":"NM", "nome":"Cintilografia da Tireoide", "codigo":"NM001"},
        ]
        write_json(EXAMTYPES_FILE, {"exam_types": seed_types}, _examtypes_lock)

    # Exames (atendimentos)
    ex = read_json(EXAMS_FILE, {"exams":[]})
    if not ex["exams"]:
        now = datetime.utcnow()
        seed_exams = [
            {"id":1,"exam_id":"E-0001","idade":45,"modalidade":"CT","exame":f"{mod_label('CT')} - Crânio","medico":"Dr. João Silva",
             "data_hora":(now - timedelta(days=3, hours=2)).isoformat(),"contraste_usado":False,"contraste_qtd":0.0,"user_email":"admin@local"},
            {"id":2,"exam_id":"E-0002","idade":61,"modalidade":"CT","exame":f"{mod_label('CT')} - Abdômen","medico":"Dra. Maria Souza",
             "data_hora":(now - timedelta(days=2, hours=4)).isoformat(),"contraste_usado":True,"contraste_qtd":80.0,"user_email":"admin@local"},
            {"id":3,"exam_id":"E-0003","idade":34,"modalidade":"RX","exame":f"{mod_label('RX')} - Tórax PA/L","medico":"Dr. João Silva",
             "data_hora":(now - timedelta(days=2)).isoformat(),"contraste_usado":False,"contraste_qtd":0.0,"user_email":"admin@local"},
            {"id":4,"exam_id":"E-0004","idade":28,"modalidade":"US","exame":f"{mod_label('US')} - Abdômen total","medico":"Dra. Carla Mendes",
             "data_hora":(now - timedelta(days=1, hours=6)).isoformat(),"contraste_usado":False,"contraste_qtd":0.0,"user_email":"admin@local"},
            {"id":5,"exam_id":"E-0005","idade":52,"modalidade":"MR","exame":f"{mod_label('MR')} - Joelho","medico":"Dr. Paulo Nogueira",
             "data_hora":(now - timedelta(hours=20)).isoformat(),"contraste_usado":True,"contraste_qtd":15.0,"user_email":"admin@local"},
            {"id":6,"exam_id":"E-0006","idade":40,"modalidade":"CT","exame":f"{mod_label('CT')} - Tórax","medico":"Dra. Maria Souza",
             "data_hora":(now - timedelta(hours=5)).isoformat(),"contraste_usado":True,"contraste_qtd":60.0,"user_email":"admin@local"},
        ]
        write_json(EXAMS_FILE, {"exams": seed_exams}, _exams_lock)

    # Médicos
    docs = read_json(DOCTORS_FILE, {"doctors":[]})
    if "doctors" not in docs: # Verifica se a chave 'doctors' existe, caso o arquivo esteja vazio mas exista.
        write_json(DOCTORS_FILE, {"doctors":[]}, _doctors_lock)

    # Logs
    lg = read_json(LOGS_FILE, {"logs":[]})
    if "logs" not in lg: # Verifica se a chave 'logs' existe.
        write_json(LOGS_FILE, {"logs":[]}, _logs_lock)

    # Settings
    if not os.path.exists(SETTINGS_FILE):
        write_json(SETTINGS_FILE, DEFAULT_SETTINGS.copy(), _settings_lock)

init_files() # Chamada de inicialização dos arquivos

# -------------------- Repositórios de Dados --------------------
# Funções de acesso e manipulação para cada entidade (Usuários, Médicos, Tipos de Exame, Exames, Logs)

# Repo: Usuários
def get_users(): return read_json(USERS_FILE, {"users":[]})["users"]
def save_users(users): write_json(USERS_FILE, {"users":users}, _users_lock)
def find_user_by_email(email):
    email=(email or "").strip().lower()
    return next((u for u in get_users() if u.get("email","").lower()==email), None)
def add_user(rec):
    users = get_users(); nxt = max([u.get("id",0) for u in users] or [0]) + 1
    rec["id"] = nxt; users.append(rec); save_users(users); return nxt
def update_user(uid, fields):
    users = get_users(); ch=False
    for u in users:
        if u.get("id")==uid:
            u.update(fields); ch=True; break
    if ch: save_users(users)
    return ch
def delete_user(uid):
    users = get_users(); b=len(users)
    users = [u for u in users if u.get("id")!=uid]
    if len(users)!=b: save_users(users); return True
    return False

# Repo: Médicos
def list_doctors(): return read_json(DOCTORS_FILE, {"doctors":[]})["doctors"]
def save_doctors(docs): write_json(DOCTORS_FILE, {"doctors":docs}, _doctors_lock)
def add_doctor(rec):
    docs = list_doctors(); nxt = max([d.get("id",0) for d in docs] or [0]) + 1
    rec["id"]=nxt; docs.append(rec); save_doctors(docs); return nxt
def update_doctor(did, fields):
    docs = list_doctors(); ch=False
    for d in docs:
        if d.get("id")==did: d.update(fields); ch=True; break
    if ch: save_doctors(docs)
    return ch
def delete_doctor(did):
    docs = list_doctors(); b=len(docs)
    docs = [d for d in docs if d.get("id")!=did]
    if len(docs)!=b: save_doctors(docs); return True
    return False

# ## MODIFICAÇÃO: Novo helper para listar médicos para o Autocomplete
def doctor_labels_for_autocomplete():
    docs = sorted(list_doctors(), key=lambda x: (x.get("nome") or "").lower())
    return [{"value": d.get("nome"), "label": d.get("nome")} for d in docs if d.get("nome")]


# Repo: Catálogo de tipos de exame
def list_exam_types(): return read_json(EXAMTYPES_FILE, {"exam_types":[]})["exam_types"]
def save_exam_types(tps): write_json(EXAMTYPES_FILE, {"exam_types":tps}, _examtypes_lock)
def add_exam_type(rec):
    tps = list_exam_types(); nxt = max([t.get("id",0) for t in tps] or [0]) + 1
    rec["id"]=nxt; tps.append(rec); save_exam_types(tps); return nxt
def update_exam_type(tid, fields):
    tps = list_exam_types(); ch=False
    for t in tps:
        if t.get("id")==tid: t.update(fields); ch=True; break
    if ch: save_exam_types(tps)
    return ch
def delete_exam_type(tid):
    tps = list_exam_types(); b=len(tps)
    tps = [t for t in tps if t.get("id")!=tid]
    if len(tps)!=b: save_exam_types(tps); return True
    return False

def examtype_labels_for(mod=None):
    """Retorna uma lista de rótulos de tipos de exame para Autocomplete, filtrada por modalidade."""
    tps = list_exam_types()
    if mod: tps = [t for t in tps if t.get("modalidade")==mod]
    return [f"{mod_label(t.get('modalidade'))} - {t.get('nome')}" if t.get("modalidade") else (t.get("nome") or "")
            for t in sorted(tps, key=lambda x: ((x.get("modalidade") or "") + " " + (x.get("nome") or "")).lower())]

# Repo: Exames
def list_exams(): return read_json(EXAMS_FILE, {"exams":[]})["exams"]
def save_exams(exms): write_json(EXAMS_FILE, {"exams":exms}, _exams_lock)
def add_exam(record):
    data = list_exams(); nxt = max([e.get("id",0) for e in data] or [0])+1
    record["id"]=nxt; data.append(record); save_exams(data); return nxt
def update_exam(exam_id, fields):
    data = list_exams(); ch=False
    for e in data:
        if e.get("id")==exam_id: e.update(fields); ch=True; break
    if ch: save_exams(data)
    return ch
def delete_exam(exam_id):
    data = list_exams(); b=len(data)
    data = [e for e in data if e.get("id")!=exam_id]
    if len(data)!=b: save_exams(data); return True
    return False

# Logs
def list_logs(): return read_json(LOGS_FILE, {"logs":[]})["logs"]
def save_logs(logs): write_json(LOGS_FILE, {"logs":logs}, _logs_lock)
def log_action(user_email, action, entity, entity_id, before=None, after=None):
    """Registra uma ação no sistema para fins de auditoria."""
    logs = list_logs()
    nxt = max([l.get("id",0) for l in logs] or [0]) + 1
    entry = {
        "id": nxt,
        "ts": datetime.utcnow().isoformat(),
        "user": user_email or "desconhecido",
        "action": action,  # create|update|delete
        "entity": entity,  # exam|doctor|user|exam_type|settings
        "entity_id": entity_id,
        "before": before,
        "after": after
    }
    logs.append(entry); save_logs(logs); return nxt

# -------------------- Funções de Data e Hora --------------------
def parse_br_date(dstr):
    """Converte uma string de data BR (DD/MM/YYYY) para objeto date."""
    return datetime.strptime(dstr, "%d/%m/%Y").date()

def format_dt_br(iso_str):
    """Formata uma string ISO de datetime para o formato BR (DD/MM/YYYY HH:MM)."""
    try: return datetime.fromisoformat(iso_str).strftime("%d/%m/%Y %H:%M")
    except ValueError: # Mais específico para erro de formato
        return iso_str # Retorna a string original se for inválida

def parse_periodo_str(periodo_str):
    """Analisa uma string de período 'DD/MM/YYYY a DD/MM/YYYY' e retorna datetimes de início e fim."""
    if not periodo_str: return None, None
    try:
        a,b = [x.strip() for x in periodo_str.split("a")]
        start = parse_br_date(a); end = parse_br_date(b)
        # Ajusta end para incluir todo o dia
        return datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
    except (ValueError, IndexError): # Mais específico para erros de split ou datetime
        print(f"Formato de período inválido: {periodo_str}")
        return None, None

# -------------------- Helpers de Validação --------------------
def validate_email_format(email):
    """Valida o formato de um email."""
    return re.match(r"[^@]+@[^@]+\.[^@]+", email)

def validate_positive_int(value, field_name, min_val=0, max_val=None):
    """Valida se um valor é um inteiro positivo dentro de um range opcional."""
    try:
        val = int(value)
        if val < min_val: return False, f"{field_name} deve ser no mínimo {min_val}."
        if max_val is not None and val > max_val: return False, f"{field_name} deve ser no máximo {max_val}."
        return True, val
    except (ValueError, TypeError):
        return False, f"{field_name} deve ser um número inteiro válido."

def validate_text_input(value, field_name, allow_empty=False, strip=True):
    """Valida se um texto não é vazio ou apenas espaços."""
    if strip: value = (value or "").strip()
    if not allow_empty and not value:
        return False, f"'{field_name}' é obrigatório."
    return True, value

def get_triggered_component_id_from_context(ctx_triggered_prop_id):
    """
    Helper para extrair o 'id' de um componente disparador de callback,
    especialmente útil para callbacks com ALL.
    """
    if not ctx_triggered_prop_id:
        return None
    try:
        # A propriedade id pode ser uma string simples ou um dict de pattern matching
        if isinstance(ctx_triggered_prop_id, str) and '.' in ctx_triggered_prop_id:
            raw_id_str = ctx_triggered_prop_id.split('.')[0]
            # Tenta avaliar como literal Python (para IDs de dict, ex: {'type': 'edit_btn', 'id': 123})
            evaluated_id = ast.literal_eval(raw_id_str)
            if isinstance(evaluated_id, dict) and 'id' in evaluated_id:
                return evaluated_id['id']
            # Caso seja um ID simples de string, sem pattern matching
            return raw_id_str
        elif isinstance(ctx_triggered_prop_id, dict) and 'id' in ctx_triggered_prop_id:
            # Já é um dicionário diretamente do pattern matching
            return ctx_triggered_prop_id['id']
        return None # Retorna None se não conseguir extrair um ID válido
    except (ValueError, SyntaxError):
        return None # Erro ao avaliar a string

# -------------------- Flask (autenticação, exportação, uploads) --------------------
server = Flask(__name__)
server.secret_key = SECRET_KEY

@server.route("/uploads/<path:filename>")
def serve_uploads(filename):
    """Serve arquivos da pasta de uploads."""
    # Garante que apenas arquivos dentro de UPLOAD_DIR sejam servidos
    # send_from_directory já trata de segurança de caminho
    return send_from_directory(UPLOAD_DIR, filename)

def login_required(view_func):
    """Decorador para rotas Flask que exigem login."""
    @wraps(view_func)
    def w(*args, **kw):
        uid, last = session.get("user_id"), session.get("last_active")
        if not uid:
            return redirect(url_for("login", next=request.path))
        try:
            # Verifica o timeout da sessão
            if last and datetime.utcnow()-datetime.fromisoformat(last) > timedelta(minutes=SESSION_TIMEOUT_MIN):
                session.clear()
                return redirect(url_for("login"))
        except ValueError: # Erro ao parsear last_active
            session.clear()
            return redirect(url_for("login"))
        session["last_active"]=datetime.utcnow().isoformat() # Atualiza a atividade da sessão
        return view_func(*args, **kw)
    return w

@server.route("/")
def root():
    """Redireciona a raiz para o aplicativo Dash."""
    return redirect("/app")

@server.route("/logout")
def logout():
    """Limpa a sessão e redireciona para o login."""
    session.clear()
    return redirect(url_for("login"))

# ## MODIFICAÇÃO: LOGIN_TEMPLATE alterado para exibir apenas o logo ou nome e dimensão customizável
LOGIN_TEMPLATE = """
<!doctype html><html><head><meta charset="utf-8">
<title>{{ portal_name }} - Login</title><meta name="viewport" content="width=device-width, initial-scale=1">
<link id="theme_login" rel="stylesheet" href="{{ theme_url }}">
<style>
html,body{height:100%;margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Arial}
.wrap{display:flex;align-items:center;justify-content:center;height:100%;background:#f6f7fb}
.card{background:#fff;padding:32px;border-radius:16px;width:360px;box-shadow:0 10px 30px rgba(0,0,0,.08)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:12px; justify-content: center;} /* Centraliza o conteúdo da brand */
.brand img{height:{{ logo_height_px }}px;width:auto;display:block;} /* ## MODIFICAÇÃO: Altura dinâmica para o logo */
h1{font-size:20px;margin:0 0 6px}
label{display:block;margin-top:12px;font-size:14px}
input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid #dcdce6;margin-top:6px}
button{margin-top:16px;width:100%;padding:12px;border-radius:12px;border:0;background:#111827;color:#fff;font-weight:600}
.error{color:#b91c1c;font-size:13px;margin-top:8px}.hint{margin-top:12px;font-size:12px;color:#6b7280}
</style></head><body><div class="wrap"><div class="card">
<div class="brand">
    {% if logo_url %}
        <img src="{{ logo_url }}">
    {% else %}
        <h1>{{ portal_name }}</h1>
    {% endif %}
</div>
<h1>Login</h1>{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="post"><label>E-mail</label><input name="email" type="email" required autofocus>
<label>Senha</label><input name="senha" type="password" required><button type="submit">Entrar</button></form>
<div class="hint">Usuário inicial: <b>admin@local</b> / <b>admin123</b></div></div></div></body></html>
"""

@server.route("/login", methods=["GET","POST"])
def login():
    """Rota de login para a aplicação."""
    settings = read_settings()
    theme_url = THEMES.get(settings.get("theme","Flatly"), THEMES["Flatly"])
    logo_url = None
    if settings.get("logo_file"):
        logo_url = url_for("serve_uploads", filename=settings["logo_file"]) + f"?t={int(datetime.utcnow().timestamp())}"
    
    error_message = None

    if request.method=="POST":
        email=request.form.get("email","").strip().lower()
        senha=request.form.get("senha","")
        
        # Validação básica de email no login
        if not validate_email_format(email):
            error_message = "Formato de e-mail inválido."
        else:
            u=find_user_by_email(email)
            if u and check_password_hash(u.get("senha_hash",""), senha):
                session.update({"user_id":u["id"],"user_email":u["email"],"user_name":u["nome"],
                                "perfil":u.get("perfil","user"),"last_active":datetime.utcnow().isoformat()})
                return redirect("/app")
            else:
                error_message = "Credenciais inválidas."

    # ## MODIFICAÇÃO: Passa a altura do logo para o template de login
    return render_template_string(LOGIN_TEMPLATE, error=error_message,
                                  portal_name=settings.get("portal_name","Portal Radiológico"),
                                  theme_url=theme_url, logo_url=logo_url,
                                  logo_height_px=settings.get("logo_height_px"))

@server.route("/export.csv")
@login_required
def export_csv():
    """Exporta dados de exames para CSV, com filtros de data."""
    start_str, end_str = request.args.get("start"), request.args.get("end")
    df = pd.DataFrame(list_exams())
    
    # Define colunas padrão se o DataFrame estiver vazio
    if df.empty:
        df = pd.DataFrame(columns=["id","exam_id","idade","modalidade","exame","medico","data_hora","contraste_usado","contraste_qtd","user_email"])
    
    if not df.empty:
        # Tenta converter 'data_hora' para datetime, tratando erros
        df["data_hora"] = pd.to_datetime(df["data_hora"], errors="coerce")
        
        # Aplica filtros de data
        if start_str:
            is_valid, start_dt = parse_periodo_str(f"{start_str} a {start_str}") # Reutiliza a função de parse para validação
            if is_valid: df = df[df["data_hora"] >= start_dt]
            
        if end_str:
            is_valid, _, end_dt = parse_periodo_str(f"{end_str} a {end_str}")
            if is_valid: df = df[df["data_hora"] <= end_dt]

        # Formata a coluna 'data_hora' para o CSV
        df["data_hora"] = df["data_hora"].dt.strftime("%d/%m/%Y %H:%M").fillna("")

    # Garante que todas as colunas esperadas estejam presentes
    cols=["id","exam_id","idade","modalidade","exame","medico","data_hora","contraste_usado","contraste_qtd","user_email"]
    for c in cols:
        if c not in df.columns: df[c]=None
    
    # Preenche valores NaN/NaT antes de exportar
    df = df[cols].fillna('')

    # Cria a resposta CSV
    resp = make_response(df.to_csv(index=False, encoding="utf-8-sig"))
    resp.headers["Content-Disposition"]="attachment; filename=exams_export.csv"
    resp.mimetype="text/csv"
    return resp

@server.route("/health")
def health():
    """Endpoint de saúde para verificar se o servidor está ativo."""
    return {"status":"ok","time":datetime.utcnow().isoformat()}

# -------------------- Dash Application --------------------
external_stylesheets=[dbc.themes.BOOTSTRAP]
dash_app = dash.Dash(__name__, server=server, url_base_pathname="/app/",
                     external_stylesheets=external_stylesheets, suppress_callback_exceptions=True,
                     title="Portal Radiológico (Local JSON)")

def current_user():
    """Retorna o objeto do usuário logado na sessão Dash."""
    uid=session.get("user_id")
    if not uid: return None
    return next((u for u in get_users() if u.get("id")==uid), None)

def guard(children):
    """Componente de guarda de acesso para o Dash, redirecionando para login se não autenticado."""
    if not session.get("user_id"):
        return html.Div(dbc.Alert(["Você precisa estar logado. ", html.A("Ir para o login", href="/login")], color="warning"), style={"padding":"2rem"})
    return children

# -------------------- Componentes de UI (Cabeçalho, Cards, Tabelas) --------------------
def brand_title_component(settings):
    """Componente do título da marca para o cabeçalho."""
    portal_name = settings.get("portal_name", "Portal Radiológico")
    return html.Span(
        portal_name,
        className="navbar-brand fw-semibold text-uppercase",
        style={"letterSpacing": ".04em", "margin": 0}
    )

def build_user_menu():
    """Construir o menu suspenso do usuário logado."""
    name = session.get("user_name") or "Usuário"
    email = session.get("user_email") or ""
    return dbc.DropdownMenu(
        label=f"👤 {name}",
        align_end=True,
        children=[
            dbc.DropdownMenuItem(f"Conectado como {email}", header=True),
            dbc.DropdownMenuItem("Trocar senha…", id="open_pw_modal"),
            dbc.DropdownMenuItem(divider=True),
            dbc.DropdownMenuItem("Sair", id="open_logout_modal"),
        ],
        className="ms-2"
    )

def navbar():
    """Componente da barra de navegação principal da aplicação."""
    return dbc.Navbar(
        dbc.Container(
            [
                html.Div(
                    brand_title_component(read_settings()),
                    id="brand_center",
                    className="position-absolute start-50 translate-middle-x"
                ),
                html.Div(build_user_menu(), id="user_menu", className="ms-auto")
            ],
            fluid=True,
            className="position-relative"
        ),
        dark=True,
        style={
            "background": "linear-gradient(90deg, #0f172a 0%, #111827 40%, #0b2447 100%)",
            "boxShadow": "0 6px 20px rgba(0,0,0,.18)",
            "borderBottom": "1px solid rgba(255,255,255,.06)"
        },
        className="mb-3"
    )

def cadastro_card():
    """Card para o formulário de Cadastro de Exame."""
    return dbc.Card([
        dbc.CardHeader("Cadastro de Exame (Atendimento)", className="fw-semibold"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(dbc.Input(id="exam_id", placeholder="ID do exame (obrigatório)", type="text", maxLength=50), md=3), # Add maxLength
                dbc.Col(dcc.Dropdown(id="modalidade", options=[{"label":mod_label(m),"value":m} for m in MODALIDADES], placeholder="Modalidade (obrigatório)"), md=3),
                dbc.Col(dmc.Autocomplete(id="exame_auto", placeholder="Exame (catálogo ou digite, obrigatório)", data=[], limit=50), md=6),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col(dmc.DateTimePicker(
                    id="data_dt",
                    placeholder="Selecione data e hora (obrigatório)",
                    valueFormat="DD/MM/YYYY HH:mm",
                    withSeconds=False,
                ), md=6),
                # ## MODIFICAÇÃO: Input de texto para Autocomplete de médicos
                dbc.Col(dmc.Autocomplete(id="medico_auto", placeholder="Médico responsável (obrigatório)", data=[], limit=50), md=6),
            ], className="mb-3"),
            dbc.Row([
                dbc.Col(dbc.Input(id="idade", placeholder="Idade (0-120)", type="number", min=0, max=120), md=3),
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

def filtros_card():
    """Card para os filtros do Dashboard."""
    return dbc.Card([
        dbc.CardHeader("Filtros do Dashboard"),
        dbc.CardBody(dbc.Row([
            dbc.Col(dcc.Dropdown(id="filtro_modalidade", options=[{"label":mod_label(m),"value":m} for m in MODALIDADES], multi=True, placeholder="Modalidades"), md=4),
            dbc.Col(dbc.Input(id="filtro_medico", placeholder="Médico (contém)", type="text"), md=4),
            dbc.Col(dbc.Input(id="filtro_periodo", placeholder="Período (DD/MM/YYYY a DD/MM/YYYY)", type="text"), md=4),
        ]))
    ], className="shadow-sm")

def kpis_graficos():
    """Layout para os KPIs e gráficos do Dashboard."""
    return html.Div([
        dbc.Row([
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Total de Exames"), html.H2(id="kpi_total")]), className="shadow-sm"), md=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("% c/ Contraste"), html.H2(id="kpi_contraste")]), className="shadow-sm"), md=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Idade Média"), html.H2(id="kpi_idade_media")]), className="shadow-sm"), md=3),
            dbc.Col(dbc.Card(dbc.CardBody([html.H6("Idade Mediana"), html.H2(id="kpi_idade_mediana")]), className="shadow-sm"), md=3),
        ], className="mb-3"),
        # dcc.Loading para melhorar a UX durante o carregamento dos gráficos
        dcc.Loading(children=[
            dbc.Row([dbc.Col(dcc.Graph(id="g_exames_modalidade"), md=6), dbc.Col(dcc.Graph(id="g_series_tempo"), md=6)], className="mb-3"),
            dbc.Row([dbc.Col(dcc.Graph(id="g_ranking_medicos"), md=6), dbc.Col(dcc.Graph(id="g_contraste_pie"), md=6)])
        ], type="circle"), # Tipo de loading, pode ser 'graph', 'cube', 'spin', 'default'
    ])

def exams_table_component(rows):
    """Construir a tabela de exames."""
    header = html.Thead(html.Tr([html.Th("ID"),html.Th("Exam ID"),html.Th("Modalidade"),html.Th("Exame"),html.Th("Médico"),
                                 html.Th("Data/Hora"),html.Th("Idade"),html.Th("Contraste"),html.Th("Ações")]))
    body=[]
    for e in rows:
        badge = dbc.Badge("Sim", color="success") if e.get("contraste_usado") else dbc.Badge("Não", color="secondary")
        body.append(html.Tr([
            html.Td(e.get("id")), html.Td(e.get("exam_id")), html.Td(mod_label(e.get("modalidade"))), html.Td(e.get("exame")),
            html.Td(e.get("medico")), html.Td(format_dt_br(e.get("data_hora"))), html.Td(e.get("idade")), html.Td(badge),
            html.Td(html.Div([
                dbc.Button("Editar", id={"type":"edit_btn","id":e.get("id")}, size="sm", color="warning", className="me-2"),
                dbc.Button("Excluir", id={"type":"del_btn","id":e.get("id")}, size="sm", color="danger")
            ]))
        ]))
    return dbc.Table([header, html.Tbody(body)], bordered=True, hover=True, responsive=True, striped=True, className="align-middle")

def ger_users_tab():
    """Conteúdo da aba 'Usuários' do menu Gerencial."""
    return dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Novo Usuário"),
            dbc.CardBody([
                dbc.Input(id="nu_nome", placeholder="Nome completo", className="mb-2", maxLength=100),
                dbc.Input(id="nu_email", placeholder="E-mail", type="email", className="mb-2", maxLength=100),
                dcc.Dropdown(id="nu_perfil", options=[{"label":"Administrador","value":"admin"},{"label":"Usuário","value":"user"}],
                             placeholder="Perfil", className="mb-2"),
                dbc.Input(id="nu_modalidades", placeholder='Modalidades permitidas (ex: "*" ou RX,CT,MR)', className="mb-2", maxLength=50),
                dbc.Input(id="nu_senha", placeholder="Senha", type="password", className="mb-2", minLength=6), # minLength para senha
                dbc.Button("Criar Usuário", id="btn_nu_criar", color="primary"),
                html.Div(id="nu_feedback", className="mt-3")
            ])
        ], className="shadow-sm"), md=4),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Usuários Cadastrados"),
            dbc.CardBody([html.Div(id="users_table")])
        ], className="shadow-sm"), md=8)
    ])

def users_table_component():
    """Construir a tabela de usuários."""
    users = sorted(get_users(), key=lambda x: x.get("id",0))
    header = html.Thead(html.Tr([html.Th("ID"), html.Th("Nome"), html.Th("E-mail"), html.Th("Perfil"), html.Th("Modalidades"), html.Th("Ações")]))
    body=[]
    for u in users:
        mods = u.get("modalidades_permitidas","")
        body.append(html.Tr([
            html.Td(u.get("id")), html.Td(u.get("nome")), html.Td(u.get("email")),
            html.Td(u.get("perfil")), html.Td(mods),
            html.Td(html.Div([
                dbc.Button("Editar", id={"type":"user_edit_btn","id":u.get("id")}, size="sm", color="warning", className="me-2"),
                dbc.Button("Excluir", id={"type":"user_del_btn","id":u.get("id")}, size="sm", color="danger")
            ]))
        ]))
    return dbc.Table([header, html.Tbody(body)], bordered=True, hover=True, responsive=True, striped=True, className="align-middle")

def ger_doctors_tab():
    """Conteúdo da aba 'Médicos' do menu Gerencial."""
    return dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Novo Médico"),
            dbc.CardBody([
                dbc.Input(id="nd_nome", placeholder="Nome do médico", className="mb-2", maxLength=100),
                dbc.Input(id="nd_crm", placeholder="CRM (opcional)", className="mb-2", maxLength=20),
                dbc.Button("Adicionar Médico", id="btn_nd_criar", color="primary"),
                html.Div(id="nd_feedback", className="mt-3")
            ])
        ], className="shadow-sm"), md=4),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Médicos Cadastrados"),
            dbc.CardBody([html.Div(id="doctors_table")])
        ], className="shadow-sm"), md=8)
    ])

def doctors_table_component():
    """Construir a tabela de médicos."""
    docs = sorted(list_doctors(), key=lambda x: (x.get("nome") or "").lower())
    header = html.Thead(html.Tr([html.Th("ID"), html.Th("Nome"), html.Th("CRM"), html.Th("Ações")]))
    body=[]
    for d in docs:
        body.append(html.Tr([
            html.Td(d.get("id")), html.Td(d.get("nome")), html.Td(d.get("crm")),
            html.Td(html.Div([
                dbc.Button("Editar", id={"type":"doc_edit_btn","id":d.get("id")}, size="sm", color="warning", className="me-2"),
                dbc.Button("Excluir", id={"type":"del_btn","id":d.get("id")}, size="sm", color="danger")
            ]))
        ]))
    return dbc.Table([header, html.Tbody(body)], bordered=True, hover=True, responsive=True, striped=True, className="align-middle")

def ger_examtypes_tab():
    """Conteúdo da aba 'Catálogo de Exames' do menu Gerencial."""
    return dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Novo Tipo de Exame"),
            dbc.CardBody([
                dcc.Dropdown(id="nt_modalidade", options=[{"label":mod_label(m),"value":m} for m in MODALIDADES],
                             placeholder="Modalidade", className="mb-2"),
                dbc.Input(id="nt_nome", placeholder="Nome do exame (ex.: Abdômen, Crânio)", className="mb-2", maxLength=100),
                dbc.Input(id="nt_codigo", placeholder="Código (opcional)", className="mb-3", maxLength=20),
                dbc.Button("Adicionar ao Catálogo", id="btn_nt_criar", color="primary"),
                html.Div(id="nt_feedback", className="mt-3")
            ])
        ], className="shadow-sm"), md=4),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Catálogo de Exames"),
            dbc.CardBody([html.Div(id="examtypes_table")])
        ], className="shadow-sm"), md=8)
    ])

def examtypes_table_component():
    """Construir a tabela de tipos de exame."""
    tps = sorted(list_exam_types(), key=lambda x: ((x.get("modalidade") or "") + " " + (x.get("nome") or "")).lower())
    header = html.Thead(html.Tr([html.Th("ID"), html.Th("Modalidade"), html.Th("Nome"), html.Th("Código"), html.Th("Ações")]))
    body=[]
    for t in tps:
        body.append(html.Tr([
            html.Td(t.get("id")), html.Td(mod_label(t.get("modalidade"))), html.Td(t.get("nome")), html.Td(t.get("codigo")),
            html.Td(html.Div([
                dbc.Button("Editar", id={"type":"ext_edit_btn","id":t.get("id")}, size="sm", color="warning", className="me-2"),
                dbc.Button("Excluir", id={"type":"ext_del_btn","id":t.get("id")}, size="sm", color="danger")
            ]))
        ]))
    return dbc.Table([header, html.Tbody(body)], bordered=True, hover=True, responsive=True, striped=True, className="align-middle")

def ger_custom_tab():
    """Conteúdo da aba 'Customização' do menu Gerencial."""
    settings = read_settings()
    theme_value = settings.get("theme","Flatly")
    portal_name = settings.get("portal_name","Portal Radiológico")
    logo_file = settings.get("logo_file")
    logo_height_px = settings.get("logo_height_px", DEFAULT_SETTINGS["logo_height_px"]) # ## MODIFICAÇÃO: Obtém altura do logo

    theme_cards = dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Tema"),
            dbc.CardBody([
                dcc.RadioItems(
                    id="cust_theme",
                    options=[{"label": k, "value": k} for k in THEMES.keys()],
                    value=theme_value,
                    inputStyle={"marginRight":"6px"},
                    labelStyle={"display":"block", "marginBottom":"6px"}
                ),
                dbc.Alert("A seleção de tema aplica um preview imediato no app. Clique em Salvar para persistir.", color="info", className="mt-2")
            ])
        ], className="shadow-sm h-100"), md=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Preview do Tema"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(dbc.Card([
                        dbc.CardHeader("Exemplo de Card"),
                        dbc.CardBody([
                            html.P("Botões:"),
                            dbc.Button("Primário", color="primary", className="me-2 mb-2"),
                            dbc.Button("Sucesso", color="success", className="me-2 mb-2"),
                            dbc.Button("Escuro", color="dark", className="mb-2"),
                            html.Hr(),
                            dbc.Alert("Este é um alerta de informação.", color="info"),
                            dbc.Progress(value=66, className="mt-2")
                        ])
                    ])),
                ])
            ])
        ], className="shadow-sm h-100"), md=6)
    ], className="g-3")

    brand_card = dbc.Card([
        dbc.CardHeader("Identidade Visual (Login)"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col(dbc.Input(id="cust_portal_name", value=portal_name, placeholder="Nome do portal", maxLength=100), md=8),
                # ## MODIFICAÇÃO: Input para altura do logo
                dbc.Col(dbc.Input(id="cust_logo_height_px", value=logo_height_px, type="number", min=10, max=200, step=1, placeholder="Altura do logo (px)"), md=4),
            ], className="mb-3"),
            dbc.Row([ # ## MODIFICAÇÃO: Nova linha para o upload e preview do logo
                dbc.Col(html.Div([
                    dcc.Upload(
                        id="cust_logo_upload",
                        children=html.Div(["Arraste ou clique para enviar o logo (PNG/JPG/SVG)"]),
                        multiple=False, accept="image/*",
                        style={"border":"1px dashed #9ca3af","borderRadius":"10px","padding":"10px","textAlign":"center"}
                    ),
                    dcc.Store(id="cust_logo_tmp"), # Store para guardar o logo temporariamente antes de salvar
                ]), md=4),
                dbc.Col(html.Div([
                    html.Small("Preview do logo atual:"),
                    html.Div([
                        html.Img(
                            id="cust_logo_preview",
                            src=(f"/uploads/{logo_file}" if logo_file else None),
                            style={"height":f"{logo_height_px}px","display":"block","marginTop":"6px"} # ## MODIFICAÇÃO: Altura dinâmica no preview
                        )
                    ])
                ]), md=8)
            ], className="mb-3"),
            html.Hr(),
            dbc.Button("Salvar customização", id="cust_save", color="primary"),
            html.Div(id="cust_feedback", className="mt-3"),
        ])
    ], className="shadow-sm")

    return html.Div([theme_cards, html.Hr(), brand_card])

def ger_logs_tab():
    """Conteúdo da aba 'Logs' do menu Gerencial."""
    logs = sorted(list_logs(), key=lambda x: x.get("id",0), reverse=True)[:300] # Limita a 300 logs para performance
    if not logs:
        table = dbc.Alert("Sem eventos registrados ainda.", color="secondary")
    else:
        header = html.Thead(html.Tr([html.Th("Quando (UTC)"), html.Th("Usuário"), html.Th("Ação"), html.Th("Entidade"), html.Th("ID"), html.Th("Resumo")]))
        body=[]
        for l in logs:
            resumo = "-"
            if l.get("action")=="update" and l.get("before") and l.get("after"):
                diffs = []
                # Compara os campos para identificar as mudanças
                for k,v in (l["after"] or {}).items():
                    bv = (l["before"] or {}).get(k, None)
                    if v != bv and k not in ["senha_hash"]: # Ignora hash de senha
                        diffs.append(k)
                resumo = ", ".join(diffs) if diffs else "Nenhuma mudança visível" # Feedback mais claro
            body.append(html.Tr([
                html.Td(l.get("ts")), html.Td(l.get("user")), html.Td(l.get("action")),
                html.Td(l.get("entity")), html.Td(l.get("entity_id")), html.Td(resumo)
            ]))
        table = dbc.Table([header, html.Tbody(body)], bordered=True, hover=True, striped=True, responsive=True, className="align-middle")
    return dbc.Card([dbc.CardHeader("Logs (últimos 300)"), dbc.CardBody(table)], className="shadow-sm")

def gerencial_content():
    """Layout principal do menu Gerencial, com abas para diferentes seções."""
    u = current_user()
    if not u or u.get("perfil")!="admin":
        return dbc.Alert("Acesso restrito aos administradores.", color="danger", className="mt-3")
    return html.Div([
        dbc.Tabs(id="tabs_gerencial", active_tab="g_users", class_name="mb-3", children=[
            dbc.Tab(label="Usuários", tab_id="g_users", children=[ger_users_tab()]),
            dbc.Tab(label="Médicos", tab_id="g_doctors", children=[ger_doctors_tab()]),
            dbc.Tab(label="Catálogo de Exames", tab_id="g_examtypes", children=[ger_examtypes_tab()]),
            dbc.Tab(label="Customização", tab_id="g_custom", children=[ger_custom_tab()]),
            dbc.Tab(label="Logs", tab_id="g_logs", children=[ger_logs_tab()]),
        ])
    ])

# -------------------- Layout Principal do Dash --------------------
dash_app.layout = lambda: dmc.MantineProvider(
    dmc.DatesProvider(
        settings={"locale": "pt-br"}, # Define o locale globalmente para Date/Time Pickers
        children=guard( # Aplica a guarda de acesso ao layout principal
            dbc.Container([
                html.Link(id="theme_css", rel="stylesheet", href=THEMES.get(read_settings().get("theme","Flatly"), THEMES["Flatly"])),
                dcc.Store(id="settings_store"), # Store para armazenar configurações e sincronizar UI
                navbar(), # Barra de navegação
                dbc.Tabs(
                    id="tabs",
                    active_tab="cadastro",
                    class_name="mb-3 justify-content-center",  # centraliza as abas
                    children=[
                        dbc.Tab(label="Cadastro", tab_id="cadastro", children=[cadastro_card()]),
                        dbc.Tab(label="Dashboard", tab_id="dashboard", children=[dcc.Store(id="data_cache", storage_type="session"), filtros_card(), html.Hr(), kpis_graficos()]), # data_cache para evitar recarga de dados
                        dbc.Tab(label="Exames", tab_id="exames", children=[dbc.Card([dbc.CardHeader("Exames Cadastrados"),
                            dbc.CardBody([html.Div(id="exams_feedback"), html.Div(id="exams_table")])], className="shadow-sm")]),
                        dbc.Tab(label="Gerencial", tab_id="gerencial", children=[gerencial_content()]),
                        dbc.Tab(label="Exportar", tab_id="exportar", children=[dbc.Card([dbc.CardHeader("Exportação"),
                            dbc.CardBody([html.P("Baixe CSV (datas em BR)."),
                                          dbc.Row([dbc.Col(dbc.Input(id="exp_start", placeholder="Início (DD/MM/YYYY)", type="text"), md=4),
                                                   dbc.Col(dbc.Input(id="exp_end", placeholder="Fim (DD/MM/YYYY)", type="text"), md=4),
                                                   dbc.Col(html.A("Baixar CSV", id="exp_link", href="/export.csv", className="btn btn-dark w-100"), md=4)])])], className="shadow-sm")])
                    ]
                ),
                # ----- Modais: Exame (Edição e Exclusão) -----
                dbc.Modal(id="edit_modal", is_open=False, size="lg", children=[
                    dbc.ModalHeader(dbc.ModalTitle("Editar Exame")),
                    dbc.ModalBody([
                        dcc.Store(id="edit_exam_id"), # Armazena o ID do exame sendo editado
                        dbc.Row([
                            dbc.Col(dbc.Input(id="edit_exam_id_text", placeholder="ID do exame", type="text", maxLength=50), md=3),
                            dbc.Col(dcc.Dropdown(id="edit_modalidade", options=[{"label":mod_label(m),"value":m} for m in MODALIDADES], placeholder="Modalidade"), md=3),
                            dbc.Col(dmc.Autocomplete(id="edit_exame_auto", placeholder="Exame (catálogo ou digite)", data=[], limit=50), md=6),
                        ], className="mb-3"),
                        dbc.Row([
                            dbc.Col(dmc.DateTimePicker(
                                id="edit_data_dt",
                                placeholder="Selecione data e hora",
                                valueFormat="DD/MM/YYYY HH:mm",
                                withSeconds=False,
                            ), md=6),
                            # ## MODIFICAÇÃO: Input de texto para Autocomplete de médicos no modal de edição
                            dbc.Col(dmc.Autocomplete(id="edit_medico_auto", placeholder="Médico responsável", data=[], limit=50), md=6),
                        ], className="mb-3"),
                        dbc.Row([
                            dbc.Col(dbc.Input(id="edit_idade", placeholder="Idade", type="number", min=0, max=120), md=3),
                            dbc.Col(dbc.Checklist(id="edit_contraste_usado", options=[{"label":" Usou contraste","value":"yes"}], value=[]), md=3),
                            dbc.Col(dbc.Input(id="edit_contraste_qtd", placeholder="Qtd Contraste (mL)", type="number", min=0, step=1, disabled=True), md=3),
                            dbc.Col(html.Div(), md=3)
                        ])
                    ]),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="edit_cancel", className="me-2"), dbc.Button("Salvar Alterações", id="edit_save", color="primary")])
                ]),
                dcc.Store(id="delete_exam_id"), # Armazena o ID do exame a ser excluído
                dbc.Modal(id="confirm_delete_modal", is_open=False, children=[
                    dbc.ModalHeader(dbc.ModalTitle("Confirmar exclusão de exame")),
                    dbc.ModalBody([
                        html.Div(id="delete_info", className="mb-2"),
                        dbc.Alert("Esta ação é irreversível.", color="warning", className="mb-0")
                    ]),
                    dbc.ModalFooter([
                        dbc.Button("Cancelar", id="delete_cancel", className="me-2"),
                        dbc.Button("Excluir definitivamente", id="delete_confirm", color="danger")
                    ])
                ]),
                # ----- Modais: Usuário (editar próprio password) -----
                dbc.Modal(id="change_pw_modal", is_open=False, children=[
                    dbc.ModalHeader(dbc.ModalTitle("Trocar senha")),
                    dbc.ModalBody([
                        dbc.Input(id="pw_old", type="password", placeholder="Senha atual", className="mb-2"),
                        dbc.Input(id="pw_new1", type="password", placeholder="Nova senha", className="mb-2", minLength=6),
                        dbc.Input(id="pw_new2", type="password", placeholder="Confirmar nova senha", minLength=6),
                        html.Div(id="pw_feedback", className="mt-3")
                    ]),
                    dbc.ModalFooter([
                        dbc.Button("Cancelar", id="pw_cancel_btn", className="me-2"),
                        dbc.Button("Salvar nova senha", id="pw_save_btn", color="primary")
                    ])
                ]),
                # ----- Modal: Logout -----
                dbc.Modal(id="logout_modal", is_open=False, children=[
                    dbc.ModalHeader(dbc.ModalTitle("Deseja sair do sistema?")),
                    dbc.ModalBody("Você será redirecionado para a tela de login."),
                    dbc.ModalFooter([
                        dbc.Button("Cancelar", id="logout_cancel_btn", className="me-2"),
                        dbc.Button("Sair", color="danger", href="/logout", external_link=True)  # <- força navegação Flask
                    ])
                ]),
                # ----- Modais de Gerenciamento (Usuários, Médicos, Catálogo) -----
                dbc.Modal(id="user_edit_modal", is_open=False, size="lg", children=[
                    dbc.ModalHeader(dbc.ModalTitle("Editar Usuário")),
                    dbc.ModalBody([
                        dcc.Store(id="edit_user_id"),
                        dbc.Row([
                            dbc.Col(dbc.Input(id="eu_nome", placeholder="Nome completo", maxLength=100), md=4),
                            dbc.Col(dbc.Input(id="eu_email", placeholder="E-mail", type="email", maxLength=100), md=4),
                            dbc.Col(dcc.Dropdown(id="eu_perfil", options=[{"label":"Administrador","value":"admin"},{"label":"Usuário","value":"user"}], placeholder="Perfil"), md=4),
                        ], className="mb-3"),
                        dbc.Row([
                            dbc.Col(dbc.Input(id="eu_modalidades", placeholder='Modalidades permitidas (ex: "*" ou RX,CT,MR)', maxLength=50), md=6),
                            dbc.Col(dbc.Input(id="eu_nova_senha", placeholder="Nova senha (opcional)", type="password", minLength=6), md=6),
                        ])
                    ]),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="user_edit_cancel", className="me-2"), dbc.Button("Salvar", id="user_edit_save", color="primary")])
                ]),
                dcc.Store(id="delete_user_id"),
                dbc.Modal(id="user_confirm_delete_modal", is_open=False, children=[
                    dbc.ModalHeader(dbc.ModalTitle("Excluir usuário?")),
                    dbc.ModalBody(html.Div(id="user_delete_info")),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="user_delete_cancel", className="me-2"),
                                     dbc.Button("Excluir", id="user_delete_confirm", color="danger")])
                ]),
                dbc.Modal(id="doc_edit_modal", is_open=False, size="lg", children=[
                    dbc.ModalHeader(dbc.ModalTitle("Editar Médico")),
                    dbc.ModalBody([
                        dcc.Store(id="edit_doc_id"),
                        dbc.Row([
                            dbc.Col(dbc.Input(id="ed_nome", placeholder="Nome do médico", maxLength=100), md=6),
                            dbc.Col(dbc.Input(id="ed_crm", placeholder="CRM", maxLength=20), md=6),
                        ])
                    ]),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="doc_edit_cancel", className="me-2"), dbc.Button("Salvar", id="doc_edit_save", color="primary")])
                ]),
                dcc.Store(id="delete_doc_id"),
                dbc.Modal(id="doc_confirm_delete_modal", is_open=False, children=[
                    dbc.ModalHeader(dbc.ModalTitle("Excluir médico?")),
                    dbc.ModalBody(html.Div(id="doc_delete_info")),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="doc_delete_cancel", className="me-2"),
                                     dbc.Button("Excluir", id="doc_delete_confirm", color="danger")])
                ]),
                dbc.Modal(id="ext_edit_modal", is_open=False, size="lg", children=[
                    dbc.ModalHeader(dbc.ModalTitle("Editar Tipo de Exame")),
                    dbc.ModalBody([
                        dcc.Store(id="edit_ext_id"),
                        dbc.Row([
                            dbc.Col(dcc.Dropdown(id="ext_modalidade", options=[{"label":mod_label(m),"value":m} for m in MODALIDADES], placeholder="Modalidade"), md=4),
                            dbc.Col(dbc.Input(id="ext_nome", placeholder="Nome do exame", maxLength=100), md=5),
                            dbc.Col(dbc.Input(id="ext_codigo", placeholder="Código (opcional)", maxLength=20), md=3),
                        ])
                    ]),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="ext_edit_cancel", className="me-2"), dbc.Button("Salvar", id="ext_edit_save", color="primary")])
                ]),
                dcc.Store(id="delete_ext_id"),
                dbc.Modal(id="ext_confirm_delete_modal", is_open=False, children=[
                    dbc.ModalHeader(dbc.ModalTitle("Excluir tipo de exame?")),
                    dbc.ModalBody(html.Div(id="ext_delete_info")),
                    dbc.ModalFooter([dbc.Button("Cancelar", id="ext_delete_cancel", className="me-2"),
                                     dbc.Button("Excluir", id="ext_delete_confirm", color="danger")])
                ])
            ], fluid=True, className="pb-4")
        )
    )
)

# -------------------- Callbacks de Interação da UI --------------------

# Cadastro de EXAME
@dash_app.callback(Output("contraste_qtd","disabled"), Output("contraste_qtd","value"), Input("contraste_usado","value"))
def toggle_qtd(ck):
    """Habilita/desabilita campo de quantidade de contraste com base no checkbox."""
    en = bool(ck and "yes" in ck); return (not en), (None if en else 0)

# ## MODIFICAÇÃO: Callback para carregar médicos para o Autocomplete de cadastro de exame
@dash_app.callback(
    Output("medico_auto","data"),
    Input("tabs","active_tab"), # Dispara ao mudar de aba, para garantir que esteja atualizado
    Input("btn_salvar","n_clicks"), # Dispara ao salvar um exame, caso um novo médico seja digitado
    Input("btn_nd_criar","n_clicks"), # Dispara ao criar um novo médico no Gerencial
    prevent_initial_call=False
)
def load_medico_auto_data(active_tab, n_clicks_salvar, n_clicks_criar_medico):
    return doctor_labels_for_autocomplete()

# ## MODIFICAÇÃO: Callback para carregar médicos para o Autocomplete de edição de exame
@dash_app.callback(
    Output("edit_medico_auto","data"),
    Input("edit_modal","is_open"), # Dispara quando o modal de edição abre
    Input("btn_nd_criar","n_clicks"), # Dispara ao criar um novo médico no Gerencial
    prevent_initial_call=True
)
def load_edit_medico_auto_data(is_open, n_clicks_criar_medico):
    if is_open:
        return doctor_labels_for_autocomplete()
    return no_update


@dash_app.callback(Output("exame_auto","data"), Input("modalidade","value"), prevent_initial_call=False)
def load_auto_data(mod):
    """Carrega dados para o Autocomplete de Exames com base na modalidade."""
    return examtype_labels_for(mod) if mod else examtype_labels_for(None)

@dash_app.callback(Output("edit_exame_auto","data"), Input("edit_modalidade","value"), Input("edit_modal","is_open"), prevent_initial_call=True)
def load_edit_auto_data(mod, opened):
    """Carrega dados para o Autocomplete de Exames no modal de edição."""
    return examtype_labels_for(mod) if mod else examtype_labels_for(None)

@dash_app.callback(
    Output("save_feedback","children"),
    Input("btn_salvar","n_clicks"),
    State("exam_id","value"), State("idade","value"), State("modalidade","value"),
    State("exame_auto","value"), State("medico_auto","value"), # ## MODIFICAÇÃO: Alterado de medico para medico_auto.value
    State("data_dt","value"),
    State("contraste_usado","value"), State("contraste_qtd","value"),
    prevent_initial_call=True
)
def salvar_exame(n, exam_id, idade, modalidade, exame_txt, medico, data_dt, ck, qtd):
    """Salva um novo registro de exame, com validação de campos."""
    if not session.get("user_id"): return dbc.Alert("Sessão expirada. Faça login novamente.", color="warning")

    # Validação de campos obrigatórios
    feedback_msgs = []
    is_valid_exam_id, clean_exam_id = validate_text_input(exam_id, "ID do exame")
    if not is_valid_exam_id: feedback_msgs.append(clean_exam_id)

    is_valid_idade, clean_idade = validate_positive_int(idade, "Idade", 0, 120)
    if not is_valid_idade: feedback_msgs.append(clean_idade)

    is_valid_modalidade, clean_modalidade = validate_text_input(modalidade, "Modalidade")
    if not is_valid_modalidade: feedback_msgs.append(clean_modalidade)
    elif clean_modalidade not in MODALIDADES: feedback_msgs.append("Modalidade inválida.")

    is_valid_exame, clean_exame = validate_text_input(exame_txt, "Exame")
    if not is_valid_exame: feedback_msgs.append(clean_exame)

    is_valid_medico, clean_medico = validate_text_input(medico, "Médico")
    if not is_valid_medico: feedback_msgs.append(clean_medico)

    if not data_dt: feedback_msgs.append("Data/Hora é obrigatória.")

    if feedback_msgs:
        return dbc.Alert(html.Ul([html.Li(msg) for msg in feedback_msgs]), color="danger")

    try:
        dt = datetime.fromisoformat(data_dt)
    except ValueError: # Tratamento específico para data/hora
        return dbc.Alert("Data/Hora inválida. Verifique o formato.", color="danger")

    contraste = bool(ck and "yes" in ck)
    clean_qtd = float(qtd or 0) if contraste else 0.0 # Define qtd como 0 se não usou contraste

    u=current_user()
    rec={
        "exam_id":clean_exam_id,
        "idade":clean_idade,
        "modalidade":clean_modalidade,
        "exame":clean_exame,
        "medico":clean_medico,
        "data_hora":dt.isoformat(),
        "contraste_usado":contraste,
        "contraste_qtd":clean_qtd,
        "user_email":u.get("email") if u else None
    }
    try:
        new_id = add_exam(rec)
        log_action(u.get("email") if u else None, "create", "exam", new_id, before=None, after=rec)
        return dbc.Alert("Exame salvo com sucesso!", color="success", duration=4000)
    except Exception as e:
        print(f"Erro ao salvar exame: {e}") # Loga o erro detalhado
        return dbc.Alert("Erro inesperado ao salvar. Tente novamente.", color="danger")

# Dashboard
@dash_app.callback(
    Output("data_cache","data"), # CORRIGIDO: de 'children' para 'data'
    Input("tabs","active_tab"), Input("filtro_modalidade","value"),
    Input("filtro_medico","value"), Input("filtro_periodo","value"),
)
def load_data(tab, modalidades, medico_like, periodo):
    """Carrega e filtra dados para o Dashboard, armazenando em cache."""
    if tab!="dashboard": return no_update # Evita execução desnecessária
    
    df = pd.DataFrame(list_exams())
    
    if df.empty:
        return pd.DataFrame(columns=["exam_id","idade","modalidade","exame","medico","data_hora","contraste_usado","contraste_qtd"]).to_json(orient="records")
    
    # Aplica filtros
    if modalidades: df=df[df["modalidade"].isin(modalidades)]
    if medico_like: df=df[df["medico"].str.contains(medico_like, case=False, na=False)]
    
    start, end = parse_periodo_str(periodo)
    if start or end:
        df["data_hora"] = pd.to_datetime(df["data_hora"], errors="coerce") # Coerce para evitar erros de parsing
        if start: df = df[df["data_hora"] >= start]
        if end: df = df[df["data_hora"] <= end] # Ajustado para <= end, pois parse_periodo_str já ajusta para o final do dia
    
    # Remove linhas com data_hora inválida (NaT) após a conversão, se necessário
    df = df.dropna(subset=['data_hora'])

    return df.to_json(orient="records", date_format="iso")

@dash_app.callback(
    Output("kpi_total","children"), Output("kpi_contraste","children"),
    Output("kpi_idade_media","children"), Output("kpi_idade_mediana","children"),
    Output("g_exames_modalidade","figure"), Output("g_series_tempo","figure"),
    Output("g_ranking_medicos","figure"), Output("g_contraste_pie","figure"),
    Input("data_cache","data"), # CORRIGIDO: de 'children' para 'data'
)
def update_dashboard(json_data):
    """Atualiza todos os KPIs e gráficos do Dashboard com base nos dados filtrados."""
    empty_fig = px.scatter(title="Sem dados") # Figura padrão para quando não há dados
    
    if not json_data:
        return "0","0%","-","-",empty_fig,empty_fig,empty_fig,empty_fig
    
    df = pd.read_json(json_data, orient="records")
    
    if df.empty:
        return "0","0%","-","-",empty_fig,empty_fig,empty_fig,empty_fig
    
    # Garante que 'data_hora' seja datetime
    if "data_hora" in df.columns:
        df["data_hora"] = pd.to_datetime(df["data_hora"], errors="coerce")
        df = df.dropna(subset=['data_hora']) # Remove NaT após coerção

    # Cálculos dos KPIs
    total = len(df)
    pct_contraste = float(df.get("contraste_usado", pd.Series(dtype=bool)).mean()*100.0) if "contraste_usado" in df else 0.0
    media_idade = df["idade"].mean() if "idade" in df and not df["idade"].isna().all() else None
    mediana_idade = df["idade"].median() if "idade" in df and not df["idade"].isna().all() else None

    # Geração dos gráficos
    # Exames por Modalidade
    fig_mod = px.bar(df.groupby("modalidade", as_index=False).size().rename(columns={"size":"qtd"}),
                     x="modalidade", y="qtd", title="Exames por Modalidade",
                     labels={"modalidade":"Modalidade","qtd":"Quantidade"})
    
    # Exames ao Longo do Tempo
    if "data_hora" in df.columns and not df["data_hora"].empty:
        df["dia"] = df["data_hora"].dt.date
        series_data = df.groupby("dia", as_index=False).size().rename(columns={"size":"qtd"})
        fig_series = px.line(series_data, x="dia", y="qtd", markers=True, title="Exames ao Longo do Tempo",
                             labels={"dia":"Data","qtd":"Quantidade"})
    else:
        fig_series = empty_fig.update_layout(title_text="Exames ao Longo do Tempo") # Mantém o título mesmo vazio
    
    # Ranking de Médicos
    fig_med = px.bar(df.groupby("medico", as_index=False).size().rename(columns={"size":"qtd"}).sort_values("qtd", ascending=False).head(15),
                     x="medico", y="qtd", title="Ranking de Médicos",
                     labels={"medico":"Médico","qtd":"Quantidade"})
    
    # % Exames com Contraste (Gráfico de Pizza)
    fig_pie = px.pie(df.assign(label=df.get("contraste_usado", pd.Series(dtype=bool)).map({True:"Com contraste", False:"Sem contraste"})),
                     names="label", title="% Exames com Contraste")
    
    return (f"{total}",
            f"{pct_contraste:.1f}%",
            f"{media_idade:.1f}" if media_idade is not None else "-",
            f"{mediana_idade:.1f}" if mediana_idade is not None else "-",
            fig_mod, fig_series, fig_med, fig_pie)

# Tabela de Exames
@dash_app.callback(Output("exams_table","children"), Input("tabs","active_tab"))
def render_exams_table(tab):
    """Renderiza a tabela de exames quando a aba 'Exames' está ativa."""
    if tab!="exames": return no_update
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    return exams_table_component(rows)

# Edição de EXAME
@dash_app.callback(
    Output("edit_modal","is_open"),
    Output("edit_exam_id","data"),
    Output("edit_exam_id_text","value"),
    Output("edit_modalidade","value"),
    Output("edit_exame_auto","value"),
    Output("edit_data_dt","value"),
    Output("edit_medico_auto","value"), # ## MODIFICAÇÃO: Alterado de edit_medico para edit_medico_auto
    Output("edit_idade","value"),
    Output("edit_contraste_usado","value"),
    Output("edit_contraste_qtd","value"),
    Input({"type":"edit_btn","id":ALL},"n_clicks"),
    Input("edit_cancel","n_clicks"),
    State("edit_modal","is_open"),
    prevent_initial_call=True
)
def open_edit_modal(edit_clicks, cancel_click, is_open):
    """Abre o modal de edição de exame e carrega os dados do exame selecionado."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"] # Valor do n_clicks

    if triggered_prop_id == "edit_cancel.n_clicks":
        return False, None, None, None, None, None, None, None, [], None # Limpa os campos e fecha
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate
    
    exam_id_to_edit = get_triggered_component_id_from_context(triggered_prop_id)
    if not exam_id_to_edit:
        raise dash.exceptions.PreventUpdate
    
    e = next((x for x in list_exams() if x.get("id")==exam_id_to_edit), None)
    if not e: raise dash.exceptions.PreventUpdate
    
    e_dt_value = None
    try:
        dt = datetime.fromisoformat(e.get("data_hora")); e_dt_value = dt.replace(microsecond=0).isoformat()
    except (ValueError, TypeError): # Trata erros de conversão de data
        pass
    
    ck = ["yes"] if e.get("contraste_usado") else []
    
    return (True, exam_id_to_edit, e.get("exam_id"), e.get("modalidade"), e.get("exame"),
            e_dt_value, e.get("medico"), e.get("idade"), ck, e.get("contraste_qtd"))

@dash_app.callback(Output("edit_contraste_qtd","disabled"), Input("edit_contraste_usado","value"))
def toggle_edit_qtd(ck):
    """Habilita/desabilita campo de quantidade de contraste no modal de edição."""
    return not bool(ck and "yes" in ck)

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
    State("edit_medico_auto","value"), # ## MODIFICAÇÃO: Alterado de edit_medico para edit_medico_auto
    State("edit_idade","value"),
    State("edit_contraste_usado","value"),
    State("edit_contraste_qtd","value"),
    prevent_initial_call=True
)
def save_edit(n, exam_id, exam_id_text, modalidade, exame_txt, edit_data_dt, medico, idade, ck, qtd):
    """Salva as alterações de um exame editado, com validação."""
    if not exam_id: raise dash.exceptions.PreventUpdate

    feedback_msgs = []
    is_valid_exam_id_text, clean_exam_id_text = validate_text_input(exam_id_text, "ID do exame")
    if not is_valid_exam_id_text: feedback_msgs.append(clean_exam_id_text)

    is_valid_idade, clean_idade = validate_positive_int(idade, "Idade", 0, 120)
    if not is_valid_idade: feedback_msgs.append(clean_idade)

    is_valid_modalidade, clean_modalidade = validate_text_input(modalidade, "Modalidade")
    if not is_valid_modalidade: feedback_msgs.append(clean_modalidade)
    elif clean_modalidade not in MODALIDADES: feedback_msgs.append("Modalidade inválida.")

    is_valid_exame, clean_exame = validate_text_input(exame_txt, "Exame")
    if not is_valid_exame: feedback_msgs.append(clean_exame)

    is_valid_medico, clean_medico = validate_text_input(medico, "Médico")
    if not is_valid_medico: feedback_msgs.append(clean_medico)

    if not edit_data_dt: feedback_msgs.append("Data/Hora é obrigatória.")

    if feedback_msgs:
        return True, dbc.Alert(html.Ul([html.Li(msg) for msg in feedback_msgs]), color="danger"), no_update

    try:
        dt = datetime.fromisoformat(edit_data_dt)
    except ValueError:
        return True, dbc.Alert("Data/Hora inválida. Verifique o formato.", color="danger"), no_update

    contraste = bool(ck and "yes" in ck)
    clean_qtd = float(qtd or 0) if contraste else 0.0

    before = next((x for x in list_exams() if x.get("id")==int(exam_id)), None)
    
    updated_fields = {
        "exam_id":clean_exam_id_text,
        "modalidade":clean_modalidade,
        "exame":clean_exame,
        "medico":clean_medico,
        "data_hora":dt.isoformat(),
        "idade":clean_idade,
        "contraste_usado":contraste,
        "contraste_qtd":clean_qtd
    }

    changed = update_exam(int(exam_id), updated_fields)
    
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    
    if changed:
        after = next((x for x in rows if x.get("id")==int(exam_id)), None)
        ue = session.get("user_email")
        log_action(ue, "update", "exam", int(exam_id), before=before, after=after)
        return False, dbc.Alert("Exame atualizado com sucesso!", color="success", duration=3000), exams_table_component(rows)
    else:
        return True, dbc.Alert("Nenhuma alteração aplicada.", color="secondary", duration=3000), exams_table_component(rows)

# Exclusão de EXAME
@dash_app.callback(
    Output("confirm_delete_modal","is_open"),
    Output("delete_exam_id","data"),
    Output("delete_info","children"),
    Input({"type":"del_btn","id":ALL}, "n_clicks"),
    Input("delete_cancel","n_clicks"),
    State("confirm_delete_modal","is_open"),
    prevent_initial_call=True
)
def open_delete_modal(del_clicks, cancel_click, is_open):
    """Abre o modal de confirmação de exclusão para exames."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "delete_cancel.n_clicks":
        return False, None, no_update
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    exam_id_to_delete = get_triggered_component_id_from_context(triggered_prop_id)
    if not exam_id_to_delete:
        raise dash.exceptions.PreventUpdate

    e = next((x for x in list_exams() if x.get("id")==exam_id_to_delete), None)
    if not e: raise dash.exceptions.PreventUpdate # Exame não encontrado

    info = html.Div([
        html.P([html.B(f"Exame #{e.get('id')}"), f" — ID: {e.get('exam_id')}"]),
        html.Ul([
            html.Li(f"Modalidade: {mod_label(e.get('modalidade'))}"),
            html.Li(f"Exame: {e.get('exame')}"),
            html.Li(f"Médico: {e.get('medico')}"),
            html.Li(f"Data/Hora: {format_dt_br(e.get('data_hora'))}")
        ], className="mb-0")
    ])
    return True, exam_id_to_delete, info

@dash_app.callback(
    Output("exams_feedback","children", allow_duplicate=True),
    Output("exams_table","children", allow_duplicate=True),
    Output("confirm_delete_modal","is_open", allow_duplicate=True),
    Input("delete_confirm","n_clicks"),
    State("delete_exam_id","data"),
    prevent_initial_call=True
)
def confirm_delete(n, exam_id):
    """Confirma e executa a exclusão de um exame."""
    if not n or not exam_id: raise dash.exceptions.PreventUpdate
    
    before = next((x for x in list_exams() if x.get("id")==int(exam_id)), None)
    ok = delete_exam(int(exam_id))
    
    ue = session.get("user_email")
    if ok: log_action(ue, "delete", "exam", int(exam_id), before=before, after=None)
    
    fb = dbc.Alert(f"Exame #{exam_id} excluído.", color="success", duration=3000) if ok else dbc.Alert("Não foi possível excluir.", color="danger")
    rows = sorted(list_exams(), key=lambda x: x.get("id",0), reverse=True)
    return fb, exams_table_component(rows), False

# GERENCIAL: Usuários
@dash_app.callback(
    Output("nu_feedback","children"),
    Output("users_table","children", allow_duplicate=True),
    Input("btn_nu_criar","n_clicks"),
    State("nu_nome","value"), State("nu_email","value"),
    State("nu_perfil","value"), State("nu_modalidades","value"),
    State("nu_senha","value"),
    prevent_initial_call=True
)
def criar_usuario(n, nome, email, perfil, modalidades, senha):
    """Cria um novo usuário, com validações e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin": return dbc.Alert("Acesso negado.", color="danger"), no_update

    feedback_msgs = []
    is_valid_nome, clean_nome = validate_text_input(nome, "Nome")
    if not is_valid_nome: feedback_msgs.append(clean_nome)

    is_valid_email, clean_email = validate_text_input(email, "E-mail")
    if not is_valid_email: feedback_msgs.append(clean_email)
    elif not validate_email_format(clean_email): feedback_msgs.append("Formato de e-mail inválido.")
    elif find_user_by_email(clean_email): feedback_msgs.append("E-mail já cadastrado.")

    is_valid_perfil, clean_perfil = validate_text_input(perfil, "Perfil")
    if not is_valid_perfil: feedback_msgs.append(clean_perfil)
    elif clean_perfil not in ["admin", "user"]: feedback_msgs.append("Perfil inválido.")

    is_valid_senha, clean_senha = validate_text_input(senha, "Senha")
    if not is_valid_senha: feedback_msgs.append(clean_senha)
    elif len(clean_senha) < 6: feedback_msgs.append("A senha deve ter pelo menos 6 caracteres.")

    # Modalidades permitidas pode ser vazio/nulo se for '*'
    clean_modalidades = (modalidades or "*").strip()

    if feedback_msgs:
        return dbc.Alert(html.Ul([html.Li(msg) for msg in feedback_msgs]), color="danger"), no_update

    rec = {
        "nome":clean_nome,
        "email":clean_email.lower(), # Garante email em minúsculas
        "senha_hash": generate_password_hash(clean_senha),
        "modalidades_permitidas": clean_modalidades,
        "perfil": clean_perfil,
        "id":0 # ID será atribuído pela função add_user
    }
    
    uid = add_user(rec)
    # Remove senha_hash do log para segurança
    logged_rec = {k:v for k,v in rec.items() if k!="senha_hash"}
    log_action(cu.get("email"), "create", "user", uid, before=None, after=logged_rec)
    
    return dbc.Alert(f"Usuário criado (ID {uid}).", color="success", duration=4000), users_table_component()

@dash_app.callback(Output("users_table","children"), Input("tabs_gerencial","active_tab"))
def render_users_table(tab):
    """Renderiza a tabela de usuários quando a aba 'Usuários' está ativa."""
    if tab!="g_users": return no_update
    return users_table_component()

@dash_app.callback(
    Output("user_edit_modal","is_open"),
    Output("edit_user_id","data"),
    Output("eu_nome","value"),
    Output("eu_email","value"),
    Output("eu_perfil","value"),
    Output("eu_modalidades","value"),
    Input({"type":"user_edit_btn","id":ALL},"n_clicks"),
    Input("user_edit_cancel","n_clicks"),
    prevent_initial_call=True
)
def open_user_edit(edit_clicks, cancel_click):
    """Abre o modal de edição de usuário e carrega os dados."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "user_edit_cancel.n_clicks":
        return False, None, None, None, None, None
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    user_id_to_edit = get_triggered_component_id_from_context(triggered_prop_id)
    if not user_id_to_edit:
        raise dash.exceptions.PreventUpdate
    
    u = next((x for x in get_users() if x.get("id")==user_id_to_edit), None)
    if not u: raise dash.exceptions.PreventUpdate
    
    return (True, user_id_to_edit, u.get("nome"), u.get("email"),
            u.get("perfil"), u.get("modalidades_permitidas"))

@dash_app.callback(
    Output("user_edit_modal","is_open", allow_duplicate=True),
    Output("users_table","children", allow_duplicate=True),
    Input("user_edit_save","n_clicks"),
    State("edit_user_id","data"),
    State("eu_nome","value"), State("eu_email","value"),
    State("eu_perfil","value"), State("eu_modalidades","value"),
    State("eu_nova_senha","value"),
    prevent_initial_call=True
)
def save_user_edit(n, uid, nome, email, perfil, modalidades, nova_senha):
    """Salva as alterações de um usuário editado, com validação e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin": raise dash.exceptions.PreventUpdate
    if not uid: raise dash.exceptions.PreventUpdate

    feedback_msgs = []
    is_valid_nome, clean_nome = validate_text_input(nome, "Nome")
    if not is_valid_nome: feedback_msgs.append(clean_nome)

    is_valid_email, clean_email = validate_text_input(email, "E-mail")
    if not is_valid_email: feedback_msgs.append(clean_email)
    elif not validate_email_format(clean_email): feedback_msgs.append("Formato de e-mail inválido.")
    # Verifica se o email já existe para outro usuário (exceto o próprio)
    existing_user = find_user_by_email(clean_email)
    if existing_user and existing_user["id"] != uid:
        feedback_msgs.append("E-mail já cadastrado por outro usuário.")

    is_valid_perfil, clean_perfil = validate_text_input(perfil, "Perfil")
    if not is_valid_perfil: feedback_msgs.append(clean_perfil)
    elif clean_perfil not in ["admin", "user"]: feedback_msgs.append("Perfil inválido.")

    if nova_senha and len(nova_senha) < 6: feedback_msgs.append("A nova senha deve ter pelo menos 6 caracteres.")

    if feedback_msgs:
        # Não fecha o modal, exibe feedback no modal
        return True, dbc.Alert(html.Ul([html.Li(msg) for msg in feedback_msgs]), color="danger"), no_update

    before = next((x for x in get_users() if x.get("id")==int(uid)), None)
    
    fields = {
        "nome":clean_nome,
        "email":clean_email.lower(),
        "perfil": clean_perfil,
        "modalidades_permitidas": (modalidades or "*").strip()
    }
    if nova_senha: fields["senha_hash"] = generate_password_hash(nova_senha)
    
    ok = update_user(int(uid), fields)
    
    if ok:
        after = next((x for x in get_users() if x.get("id")==int(uid)), None)
        # Remove senha_hash do log para segurança
        b_clean = {k: v for k,v in (before or {}).items() if k!="senha_hash"}
        a_clean = {k: v for k,v in (after or {}).items() if k!="senha_hash"}
        log_action(cu.get("email"), "update", "user", int(uid), before=b_clean, after=a_clean)
        return False, dbc.Alert("Usuário atualizado com sucesso!", color="success", duration=3000), users_table_component()
    else:
        return True, dbc.Alert("Nenhuma alteração aplicada ou erro ao atualizar.", color="secondary", duration=3000), users_table_component()

@dash_app.callback(
    Output("user_confirm_delete_modal","is_open"),
    Output("delete_user_id","data"),
    Output("user_delete_info","children"),
    Input({"type":"user_del_btn","id":ALL},"n_clicks"),
    Input("user_delete_cancel","n_clicks"),
    prevent_initial_call=True
)
def open_user_del(del_clicks, cancel_click):
    """Abre o modal de confirmação de exclusão para usuários."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "user_delete_cancel.n_clicks": return False, None, no_update
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    user_id_to_delete = get_triggered_component_id_from_context(triggered_prop_id)
    if not user_id_to_edit:
        raise dash.exceptions.PreventUpdate
    
    u = next((x for x in get_users() if x.get("id")==user_id_to_delete), None)
    if not u: raise dash.exceptions.PreventUpdate
    
    info = html.Div([html.P([html.B(f"Usuário #{u.get('id')}"), f" — {u.get('nome')} ({u.get('email')})"]),
                     dbc.Alert("Atenção: você não poderá desfazer.", color="warning", className="mb-0")]) 
    return True, user_id_to_delete, info

@dash_app.callback(
    Output("users_table","children", allow_duplicate=True),
    Output("user_confirm_delete_modal","is_open", allow_duplicate=True),
    Input("user_delete_confirm","n_clicks"),
    State("delete_user_id","data"),
    prevent_initial_call=True
)
def confirm_user_del(n, uid):
    """Confirma e executa a exclusão de um usuário."""
    cu = current_user()
    if not n or not uid: raise dash.exceptions.PreventUpdate
    
    # Não permitir que o usuário logado exclua a si mesmo
    if cu and cu.get("id")==int(uid):
        return dbc.Alert("Você não pode excluir o próprio usuário logado.", color="danger"), True # Mantém o modal aberto
    
    before = next((x for x in get_users() if x.get("id")==int(uid)), None)
    ok = delete_user(int(uid))
    
    # Remove senha_hash do log para segurança
    if ok: log_action(cu.get("email") if cu else None, "delete", "user", int(uid), before={k:v for k,v in (before or {}).items() if k!="senha_hash"}, after=None)
    
    return users_table_component(), False

# GERENCIAL: Médicos
@dash_app.callback(
    Output("nd_feedback","children"),
    Output("doctors_table","children", allow_duplicate=True),
    Input("btn_nd_criar","n_clicks"),
    State("nd_nome","value"), State("nd_crm","value"),
    prevent_initial_call=True
)
def criar_medico(n, nome, crm):
    """Cria um novo médico, com validação e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin": return dbc.Alert("Acesso negado.", color="danger"), no_update
    
    is_valid_nome, clean_nome = validate_text_input(nome, "Nome")
    if not is_valid_nome: return dbc.Alert(clean_nome, color="danger"), no_update
    
    clean_crm = (crm or "").strip() or None # CRM é opcional

    rec = {"nome": clean_nome, "crm": clean_crm, "id":0}
    did = add_doctor(rec)
    log_action(cu.get("email"), "create", "doctor", did, before=None, after=rec)
    return dbc.Alert(f"Médico criado (ID {did}).", color="success", duration=3000), doctors_table_component()

@dash_app.callback(Output("doctors_table","children"), Input("tabs_gerencial","active_tab"))
def render_doctors_table(tab):
    """Renderiza a tabela de médicos quando a aba 'Médicos' está ativa."""
    if tab!="g_doctors": return no_update
    return doctors_table_component()

@dash_app.callback(
    Output("doc_edit_modal","is_open"),
    Output("edit_doc_id","data"),
    Output("ed_nome","value"),
    Output("ed_crm","value"),
    Input({"type":"doc_edit_btn","id":ALL},"n_clicks"),
    Input("doc_edit_cancel","n_clicks"),
    prevent_initial_call=True
)
def open_doc_edit(edit_clicks, cancel_click):
    """Abre o modal de edição de médico e carrega os dados."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "doc_edit_cancel.n_clicks": return False, None, None, None
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    doc_id_to_edit = get_triggered_component_id_from_context(triggered_prop_id)
    if not doc_id_to_edit:
        raise dash.exceptions.PreventUpdate
    
    d = next((x for x in list_doctors() if x.get("id")==doc_id_to_edit), None)
    if not d: raise dash.exceptions.PreventUpdate
    
    return True, doc_id_to_edit, d.get("nome"), d.get("crm")

@dash_app.callback(
    Output("doc_edit_modal","is_open", allow_duplicate=True),
    Output("doctors_table","children", allow_duplicate=True),
    Input("doc_edit_save","n_clicks"),
    State("edit_doc_id","data"),
    State("ed_nome","value"), State("ed_crm","value"),
    prevent_initial_call=True
)
def save_doc_edit(n, did, nome, crm):
    """Salva as alterações de um médico editado, com validação e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin": raise dash.exceptions.PreventUpdate
    if not did: raise dash.exceptions.PreventUpdate

    is_valid_nome, clean_nome = validate_text_input(nome, "Nome")
    if not is_valid_nome: return True, dbc.Alert(clean_nome, color="danger"), no_update # Exibe feedback no modal

    clean_crm = (crm or "").strip() or None
    
    before = next((x for x in list_doctors() if x.get("id")==int(did)), None)
    ok = update_doctor(int(did), {"nome": clean_nome, "crm": clean_crm})
    
    if ok:
        after = next((x for x in list_doctors() if x.get("id")==int(did)), None)
        log_action(cu.get("email"), "update", "doctor", int(did), before=before, after=after)
        return False, dbc.Alert("Médico atualizado com sucesso!", color="success", duration=3000), doctors_table_component()
    else:
        return True, dbc.Alert("Nenhuma alteração aplicada ou erro ao atualizar.", color="secondary", duration=3000), doctors_table_component()

@dash_app.callback(
    Output("doc_confirm_delete_modal","is_open"),
    Output("delete_doc_id","data"),
    Output("doc_delete_info","children"),
    Input({"type":"doc_del_btn","id":ALL},"n_clicks"),
    Input("doc_delete_cancel","n_clicks"),
    prevent_initial_call=True
)
def open_doc_del(del_clicks, cancel_click):
    """Abre o modal de confirmação de exclusão para médicos."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "doc_delete_cancel.n_clicks": return False, None, no_update
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    doc_id_to_delete = get_triggered_component_id_from_context(triggered_prop_id)
    if not doc_id_to_delete:
        raise dash.exceptions.PreventUpdate
    
    d = next((x for x in list_doctors() if x.get("id")==doc_id_to_delete), None)
    if not d: raise dash.exceptions.PreventUpdate
    
    info = html.Div([html.P([html.B(f"Médico #{d.get('id')}"), f" — {d.get('nome')} {f'(CRM {d.get('crm')})' if d.get('crm') else ''}"]),
                     dbc.Alert("Esta ação é irreversível.", color="warning", className="mb-0")]) 
    return True, doc_id_to_delete, info

@dash_app.callback(
    Output("doctors_table","children", allow_duplicate=True),
    Output("doc_confirm_delete_modal","is_open", allow_duplicate=True),
    Input("doc_delete_confirm","n_clicks"),
    State("delete_doc_id","data"),
    prevent_initial_call=True
)
def confirm_doc_del(n, did):
    """Confirma e executa a exclusão de um médico."""
    cu = current_user()
    if not n or not did: raise dash.exceptions.PreventUpdate
    
    before = next((x for x in list_doctors() if x.get("id")==int(did)), None)
    ok = delete_doctor(int(did))
    
    if ok: log_action(cu.get("email") if cu else None, "delete", "doctor", int(did), before=before, after=None)
    
    return doctors_table_component(), False

# GERENCIAL: Catálogo de Exames
@dash_app.callback(
    Output("nt_feedback","children"),
    Output("examtypes_table","children", allow_duplicate=True),
    Input("btn_nt_criar","n_clicks"),
    State("nt_modalidade","value"), State("nt_nome","value"), State("nt_codigo","value"),
    prevent_initial_call=True
)
def criar_tipo_exame(n, modalidade, nome, codigo):
    """Cria um novo tipo de exame no catálogo, com validação e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin": return dbc.Alert("Acesso negado.", color="danger"), no_update
    
    feedback_msgs = []
    is_valid_modalidade, clean_modalidade = validate_text_input(modalidade, "Modalidade")
    if not is_valid_modalidade: feedback_msgs.append(clean_modalidade)
    elif clean_modalidade not in MODALIDADES: feedback_msgs.append("Modalidade inválida.")

    is_valid_nome, clean_nome = validate_text_input(nome, "Nome")
    if not is_valid_nome: feedback_msgs.append(clean_nome)
    
    if feedback_msgs:
        return dbc.Alert(html.Ul([html.Li(msg) for msg in feedback_msgs]), color="danger"), no_update

    clean_codigo = (codigo or "").strip() or None # Código é opcional

    rec = {"modalidade": clean_modalidade, "nome": clean_nome, "codigo": clean_codigo, "id":0}
    tid = add_exam_type(rec)
    log_action(cu.get("email"), "create", "exam_type", tid, before=None, after=rec)
    return dbc.Alert(f"Tipo de exame adicionado (ID {tid}).", color="success", duration=3000), examtypes_table_component()

@dash_app.callback(Output("examtypes_table","children"), Input("tabs_gerencial","active_tab"))
def render_examtypes_table(tab):
    """Renderiza a tabela de tipos de exame quando a aba 'Catálogo de Exames' está ativa."""
    if tab!="g_examtypes": return no_update
    return examtypes_table_component()

@dash_app.callback(
    Output("ext_edit_modal","is_open"),
    Output("edit_ext_id","data"),
    Output("ext_modalidade","value"),
    Output("ext_nome","value"),
    Output("ext_codigo","value"),
    Input({"type":"ext_edit_btn","id":ALL},"n_clicks"),
    Input("ext_edit_cancel","n_clicks"),
    prevent_initial_call=True
)
def open_ext_edit(edit_clicks, cancel_click):
    """Abre o modal de edição de tipo de exame e carrega os dados."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "ext_edit_cancel.n_clicks": return False, None, None, None, None
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    ext_id_to_edit = get_triggered_component_id_from_context(triggered_prop_id)
    if not ext_id_to_edit:
        raise dash.exceptions.PreventUpdate
    
    t = next((x for x in list_exam_types() if x.get("id")==ext_id_to_edit), None)
    if not t: raise dash.exceptions.PreventUpdate
    
    return True, ext_id_to_edit, t.get("modalidade"), t.get("nome"), t.get("codigo")

@dash_app.callback(
    Output("ext_edit_modal","is_open", allow_duplicate=True),
    Output("examtypes_table","children", allow_duplicate=True),
    Input("ext_edit_save","n_clicks"),
    State("edit_ext_id","data"),
    State("ext_modalidade","value"), State("ext_nome","value"), State("ext_codigo","value"),
    prevent_initial_call=True
)
def save_ext_edit(n, tid, modalidade, nome, codigo):
    """Salva as alterações de um tipo de exame editado, com validação e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin": raise dash.exceptions.PreventUpdate
    if not tid: raise dash.exceptions.PreventUpdate

    feedback_msgs = []
    is_valid_modalidade, clean_modalidade = validate_text_input(modalidade, "Modalidade")
    if not is_valid_modalidade: feedback_msgs.append(clean_modalidade)
    elif clean_modalidade not in MODALIDADES: feedback_msgs.append("Modalidade inválida.")

    is_valid_nome, clean_nome = validate_text_input(nome, "Nome")
    if not is_valid_nome: feedback_msgs.append(clean_nome)
    
    if feedback_msgs:
        return True, dbc.Alert(html.Ul([html.Li(msg) for msg in feedback_msgs]), color="danger"), no_update

    clean_codigo = (codigo or "").strip() or None
    
    before = next((x for x in list_exam_types() if x.get("id")==int(tid)), None)
    ok = update_exam_type(int(tid), {"modalidade": clean_modalidade, "nome": clean_nome, "codigo": clean_codigo})
    
    if ok:
        after = next((x for x in list_exam_types() if x.get("id")==int(tid)), None)
        log_action(cu.get("email"), "update", "exam_type", int(tid), before=before, after=after)
        return False, dbc.Alert("Tipo de exame atualizado com sucesso!", color="success", duration=3000), examtypes_table_component()
    else:
        return True, dbc.Alert("Nenhuma alteração aplicada ou erro ao atualizar.", color="secondary", duration=3000), examtypes_table_component()

@dash_app.callback(
    Output("ext_confirm_delete_modal","is_open"),
    Output("delete_ext_id","data"),
    Output("ext_delete_info","children"),
    Input({"type":"ext_del_btn","id":ALL},"n_clicks"),
    Input("ext_delete_cancel","n_clicks"),
    prevent_initial_call=True
)
def open_ext_del(del_clicks, cancel_click):
    """Abre o modal de confirmação de exclusão para tipos de exame."""
    from dash import callback_context as ctx
    if not ctx.triggered: raise dash.exceptions.PreventUpdate
    
    triggered_input = ctx.triggered[0]
    triggered_prop_id = triggered_input["prop_id"]
    triggered_value = triggered_input["value"]

    if triggered_prop_id == "ext_delete_cancel.n_clicks": return False, None, no_update
    
    # Adicionada verificação explícita do valor de n_clicks
    if triggered_value is None or triggered_value == 0:
        raise dash.exceptions.PreventUpdate

    ext_id_to_delete = get_triggered_component_id_from_context(triggered_prop_id)
    if not ext_id_to_delete:
        raise dash.exceptions.PreventUpdate
    
    t = next((x for x in list_exam_types() if x.get("id")==ext_id_to_delete), None)
    if not t: raise dash.exceptions.PreventUpdate
    
    info = html.Div([html.P([html.B(f"Tipo #{t.get('id')}"), f" — {mod_label(t.get('modalidade'))} - {t.get('nome')}"]),
                     dbc.Alert("Esta ação é irreversível (não afeta exames já realizados).", color="warning", className="mb-0")]) 
    return True, ext_id_to_delete, info

@dash_app.callback(
    Output("examtypes_table","children", allow_duplicate=True),
    Output("ext_confirm_delete_modal","is_open", allow_duplicate=True),
    Input("ext_delete_confirm","n_clicks"),
    State("delete_ext_id","data"),
    prevent_initial_call=True
)
def confirm_ext_del(n, tid):
    """Confirma e executa a exclusão de um tipo de exame."""
    cu = current_user()
    if not n or not tid: raise dash.exceptions.PreventUpdate
    
    before = next((x for x in list_exam_types() if x.get("id")==int(tid)), None)
    ok = delete_exam_type(int(tid))
    
    if ok: log_action(cu.get("email") if cu else None, "delete", "exam_type", int(tid), before=before, after=None)
    
    return examtypes_table_component(), False

# Export link
@dash_app.callback(Output("exp_link","href"), Input("exp_start","value"), Input("exp_end","value"))
def update_export_link(start, end):
    """Atualiza o link de exportação CSV com os filtros de data."""
    base="/export.csv"; qs=[]
    if start: qs.append(f"start={start}")
    if end: qs.append(f"end={end}")
    return base + (("?"+"&".join(qs)) if qs else "")

# Customização
@dash_app.callback(
    Output("settings_store","data"),
    Output("theme_css","href"),
    Output("brand_center","children"),
    Input("tabs","active_tab"),
    prevent_initial_call=False
)
def load_settings_and_brand(_tab):
    """Carrega as configurações e atualiza o tema e o título da marca."""
    s = read_settings()
    theme_href = THEMES.get(s.get("theme","Flatly"), THEMES["Flatly"])
    return s, theme_href, brand_title_component(s)

@dash_app.callback(
    Output("brand_center","children", allow_duplicate=True),
    Input("settings_store","data"),
    prevent_initial_call=True
)
def sync_brand_from_store(s):
    """Sincroniza o título da marca quando as configurações são atualizadas via store."""
    return brand_title_component(s or {"portal_name": "Portal Radiológico"})

@dash_app.callback(
    Output("theme_css","href", allow_duplicate=True),
    Input("cust_theme","value"),
    prevent_initial_call=True
)
def preview_theme(theme_value):
    """Aplica o preview do tema selecionado na customização."""
    if not theme_value: raise dash.exceptions.PreventUpdate
    return THEMES.get(theme_value, THEMES["Flatly"])

@dash_app.callback(
    Output("cust_logo_tmp","data"),
    Output("cust_logo_preview","src"),
    Input("cust_logo_upload","contents"),
    State("cust_logo_upload","filename"),
    State("cust_logo_upload","last_modified"), # Adiciona last_modified para cache-busting
    prevent_initial_call=True
)
def handle_logo_upload(contents, filename, last_modified):
    """Processa o upload do logo para preview e armazenamento temporário."""
    if not contents or not filename: raise dash.exceptions.PreventUpdate
    
    # Adiciona um timestamp para garantir que o navegador recarregue a imagem em caso de atualização
    preview_src = contents + f"?t={last_modified}" if last_modified else contents

    return {"contents": contents, "filename": filename}, preview_src

def _save_logo_from_tmp(tmpdata):
    """Salva o arquivo de logo temporário para o diretório de uploads."""
    if not tmpdata: return None
    contents = tmpdata.get("contents","")
    fn = tmpdata.get("filename","logo")
    
    try:
        header, b64 = contents.split(",", 1) # Divide apenas no primeiro vírgula
        
        # Valida o tipo de imagem com base no cabeçalho base64
        if not header.startswith("data:image/"):
            print("Erro: Conteúdo não é uma imagem válida base64.")
            return None
        
        ext = "png" # Default
        mime_type = header.split(":")[1].split(";")[0]
        
        # Mapeia mime types para extensões
        if mime_type == "image/png": ext = "png"
        elif mime_type == "image/jpeg": ext = "jpg"
        elif mime_type == "image/svg+xml": ext = "svg"
        elif mime_type == "image/webp": ext = "webp"
        else: # Tipo não suportado
            print(f"Tipo de imagem '{mime_type}' não suportado. Salvando como png.")
            ext="png" # Força png para tipos desconhecidos ou inválidos
            
        raw = base64.b64decode(b64)
        
    except Exception as e:
        print(f"Erro ao decodificar base64 ou validar logo: {e}")
        return None
    
    # Remove logos antigos para evitar acúmulo
    for f in os.listdir(UPLOAD_DIR):
        if f.startswith("logo."):
            try: os.remove(os.path.join(UPLOAD_DIR, f))
            except OSError as e: # Mais específico para erros de OS
                print(f"Erro ao remover logo antigo {f}: {e}")
    
    out_name = f"logo.{ext}"
    try:
        with open(os.path.join(UPLOAD_DIR, out_name), "wb") as f:
            f.write(raw)
    except IOError as e: # Mais específico para erros de I/O
        print(f"Erro ao escrever arquivo de logo {out_name}: {e}")
        return None
    
    return out_name

@dash_app.callback(
    Output("cust_feedback","children"),
    Output("settings_store","data", allow_duplicate=True),
    Input("cust_save","n_clicks"),
    State("cust_portal_name","value"),
    State("cust_theme","value"),
    State("cust_logo_tmp","data"),
    State("cust_logo_height_px","value"), # ## MODIFICAÇÃO: Captura a altura do logo
    prevent_initial_call=True
)
def save_custom(n, portal_name, theme_value, logo_tmp, logo_height_px): # ## MODIFICAÇÃO: Adiciona logo_height_px
    """Salva as configurações de customização do portal, com validação e log."""
    cu = current_user()
    if not cu or cu.get("perfil")!="admin":
        return dbc.Alert("Acesso negado.", color="danger"), no_update
    
    s_before = read_settings()
    
    # Validação do nome do portal
    is_valid_portal_name, clean_portal_name = validate_text_input(portal_name, "Nome do portal", allow_empty=True)
    if not is_valid_portal_name: # Embora allow_empty=True, pode pegar se for só espaços
         return dbc.Alert(clean_portal_name, color="danger"), no_update

    # ## MODIFICAÇÃO: Valida a altura do logo
    is_valid_logo_height, clean_logo_height = validate_positive_int(logo_height_px, "Altura do logo", 10, 200)
    if not is_valid_logo_height:
        return dbc.Alert(clean_logo_height, color="danger"), no_update
    
    # Processa o upload do novo logo se houver dados temporários
    new_logo = s_before.get("logo_file")
    if logo_tmp:
        saved_logo_name = _save_logo_from_tmp(logo_tmp)
        if saved_logo_name:
            new_logo = saved_logo_name
        else:
            return dbc.Alert("Erro ao processar o arquivo do logo. Verifique o formato.", color="danger"), no_update

    new = {
        "portal_name": clean_portal_name or "Portal Radiológico", # Garante um nome padrão
        "theme": theme_value or "Flatly",
        "logo_file": new_logo,
        "logo_height_px": clean_logo_height # ## MODIFICAÇÃO: Salva a altura do logo
    }
    
    s_after = write_settings(new)
    log_action(cu.get("email"), "update", "settings", 1, before=s_before, after=s_after)
    return dbc.Alert("Customização salva com sucesso!", color="success", duration=3000), s_after

# Menu do usuário: trocar senha / logout
@dash_app.callback(
    Output("change_pw_modal","is_open"),
    Output("pw_feedback","children"),
    Input("open_pw_modal","n_clicks"),
    Input("pw_cancel_btn","n_clicks"),
    prevent_initial_call=True
)
def open_close_pw_modal(open_click, cancel_click):
    """Controla a abertura e fechamento do modal de troca de senha."""
    from dash import callback_context as ctx
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    trig = ctx.triggered[0]["prop_id"]
    if trig == "open_pw_modal.n_clicks" and (open_click or 0) > 0:
        return True, "" # Limpa feedback ao abrir
    if trig == "pw_cancel_btn.n_clicks" and (cancel_click or 0) > 0:
        return False, "" # Limpa feedback ao cancelar
    raise dash.exceptions.PreventUpdate

@dash_app.callback(
    Output("change_pw_modal","is_open", allow_duplicate=True),
    Output("pw_feedback","children", allow_duplicate=True),
    Input("pw_save_btn","n_clicks"),
    State("pw_old","value"), State("pw_new1","value"), State("pw_new2","value"),
    prevent_initial_call=True
)
def save_new_password(n, pw_old, pw_new1, pw_new2):
    """Salva a nova senha do usuário, com validações de segurança."""
    if not n: raise dash.exceptions.PreventUpdate
    u = current_user()
    if not u: return False, dbc.Alert("Sessão expirada.", color="danger")
    
    if not pw_old or not pw_new1 or not pw_new2:
        return True, dbc.Alert("Preencha todos os campos.", color="danger")
    
    if not check_password_hash(u.get("senha_hash",""), pw_old):
        return True, dbc.Alert("Senha atual incorreta.", color="danger")
    
    if pw_new1 != pw_new2:
        return True, dbc.Alert("A confirmação da nova senha não confere.", color="danger")
    
    if len(pw_new1) < 6:
        return True, dbc.Alert("A nova senha deve ter pelo menos 6 caracteres.", color="danger")
    
    update_user(u["id"], {"senha_hash": generate_password_hash(pw_new1)})
    log_action(u.get("email"), "update", "user", u["id"], before=None, after={"password_changed": True})
    
    return False, dbc.Alert("Senha alterada com sucesso!", color="success", duration=3000)

@dash_app.callback(
    Output("logout_modal","is_open"),
    Input("open_logout_modal","n_clicks"),
    Input("logout_cancel_btn","n_clicks"),
    prevent_initial_call=True
)
def open_close_logout_modal(open_click, cancel_click):
    """Controla a abertura e fechamento do modal de logout."""
    from dash import callback_context as ctx
    if not ctx.triggered:
        raise dash.exceptions.PreventUpdate
    trig = ctx.triggered[0]["prop_id"]
    if trig == "open_logout_modal.n_clicks" and (open_click or 0) > 0:
        return True
    if trig == "logout_cancel_btn.n_clicks" and (cancel_click or 0) > 0:
        return False
    raise dash.exceptions.PreventUpdate

# -------------------- Início do Aplicativo --------------------
#if __name__=="__main__":
#    dash_app.run(port=int(os.getenv("PORT", "8050")), debug=False)

if __name__=="__main__":
    dash_app.run(
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8050")),
        debug=os.getenv("DEBUG", "False").lower()=="true"
    )

