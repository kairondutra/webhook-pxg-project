import json
import os
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import requests
import streamlit as st

# ====================== CONFIGURAÇÃO INICIAL ======================
load_dotenv()

st.set_page_config(
    page_title="Enemy Dashboard",
    page_icon="👻",
    layout="wide"
)

# ====================== PROTEÇÃO POR SENHA ======================
def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if st.session_state["authenticated"]:
        return True

    st.title("👻 Enemy Dashboard")
    st.caption("Acesso restrito")

    password = st.text_input("Digite a senha para entrar", type="password")

    if st.button("Entrar", type="primary"):
        correct_password = st.secrets.get("APP_PASSWORD") or os.getenv("APP_PASSWORD")

        if password == correct_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Senha incorreta")

    st.stop()

check_password()

# ====================== CONFIGURAÇÕES ======================
WEBHOOK_URL = st.secrets.get("DISCORD_WEBHOOK_URL") or os.getenv("DISCORD_WEBHOOK_URL")

# ====================== FUNÇÕES AUXILIARES ======================
def parse_session_json(json_text: str) -> dict:
    data = json.loads(json_text)
    session = data["Session"]
    enemies = data["Enemies Defeated"]

    session_id = session.get("Session ID", len(st.session_state.sessions) + 1)
    total_kills = sum(e.get("Count", 0) for e in enemies)
    total_rares = sum(e.get("Count", 0) for e in enemies if e.get("Rare"))

    player = next((e["Player"] for e in enemies if e.get("Player")), "-")

    return {
        "id": session_id,
        "player": player,
        "start": session.get("Start", "-"),
        "duration": session.get("Duration", "-"),
        "kills": total_kills,
        "rares": total_rares,
        "kills_h": session.get("Kills per hour", 0),
        "rare_h": session.get("Rare kills per hour", 0),
        "enemies": enemies,
        "drops": data.get("Drops", []),
        "session_data": session,
        "added_at": datetime.now().strftime("%H:%M:%S")
    }

def process_enemies_dataframe(enemies_data: list, total_kills: int) -> pd.DataFrame:
    df = pd.DataFrame(enemies_data).sort_values("Count", ascending=False)

    if total_kills > 0:
        df["%"] = (df["Count"] / total_kills * 100).round(1)
    else:
        df["%"] = 0.0

    def get_tipo(row):
        name = row["Enemy"]
        if name.startswith("Shiny "):
            return "⭐ Shiny"
        if name.startswith("Enraged "):
            return "🔥 Mega"
        if row.get("Rare"):
            return "✨ Raro"
        return "Normal"

    df["Tipo"] = df.apply(get_tipo, axis=1)
    return df

def calculate_rare_rates(df: pd.DataFrame) -> list[dict]:
    normals = set(df[~df["Rare"]]["Enemy"].unique())
    rates = []

    rare_rows = df[df["Rare"]]

    for _, row in rare_rows.iterrows():
        name = row["Enemy"]

        if name.startswith("Shiny "):
            base_name = name.replace("Shiny ", "").strip()
            tipo = "Shiny"
        elif name.startswith("Enraged "):
            base_name = name.replace("Enraged ", "").strip()
            tipo = "Mega"
        else:
            base_name = name
            tipo = "Raro"

        if base_name not in normals:
            continue

        normal_c = df[(df["Enemy"] == base_name) & (~df["Rare"])]["Count"].sum()
        rare_c = row["Count"]
        total_e = normal_c + rare_c
        taxa = (total_e / rare_c) if rare_c > 0 else 0

        rates.append({
            "especie": base_name,
            "tipo": tipo,
            "normal_count": normal_c,
            "rare_count": rare_c,
            "total": total_e,
            "taxa": taxa
        })

    return rates

def format_session_datetime(session: dict) -> tuple[str, str]:
    try:
        dt_obj = datetime.strptime(session["start"], "%Y-%m-%d %H:%M:%S")
        return dt_obj.strftime("%H:%M"), dt_obj.strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        return session.get("start", "-"), datetime.now().strftime("%d/%m/%Y")

def format_duration(duration: str) -> str:
    try:
        parts = [int(p) for p in duration.split(":")]
        if len(parts) == 3:
            h, m, s = parts
            if h > 0:
                return f"{h}h {m}min"
            if m > 0:
                return f"{m}min {s}s"
            return f"{s}s"
    except (ValueError, AttributeError):
        pass
    return duration

def send_rare_embed(session: dict, df_s: pd.DataFrame, rare_rates: list[dict]) -> bool:
    # Mantém o formato original da embed de resumo, sem a parte de loot.
    horario_inicio, data_sessao = format_session_datetime(session)
    duracao_tratada = format_duration(session["duration"])
    normais_count = int(df_s[~df_s["Rare"]]["Count"].sum())
    raros_count = int(df_s[df_s["Rare"]]["Count"].sum())

    fields = [
        {
            "name": "Tempo",
            "value": f"**Início:** `{horario_inicio}`\n**Duração:** `{duracao_tratada}`",
            "inline": True
        },
        {
            "name": "Derrotados",
            "value": f"**Normais:** `{normais_count:,}`\n**Raros:** `{raros_count}`",
            "inline": True
        }
    ]

    if rare_rates:
        rates_text = ""
        for r in rare_rates:
            label = "Shiny" if r["tipo"] == "Shiny" else "Mega" if r["tipo"] == "Mega" else "Raro"
            if r["rare_count"] > 0:
                rates_text += f"• **{label} {r['especie']}**: `1` a cada **{r['taxa']:.0f}**\n"
            else:
                rates_text += f"• **{label} {r['especie']}**: _nenhum_\n"

        fields.append({
            "name": "✨ Taxa de Raros por Espécie",
            "value": rates_text.strip()[:1024],
            "inline": False
        })

    monsters_table = "```text\n"
    monsters_table += f"{'Pokémon':<16} | {'Qtd':<6} | {'%'}\n"
    monsters_table += "-" * 32 + "\n"

    for _, row in df_s.iterrows():
        name = row["Enemy"][:15]
        if name.startswith("Enraged "):
            name = "Mega " + name[8:]
        monsters_table += f"{name:<16} | {row['Count']:<6} | {row['%']}%\n"

    monsters_table += "```"
    fields.append({
        "name": "📊 Detalhamento de Inimigos",
        "value": monsters_table[:1024],
        "inline": False
    })

    payload = {
        "embeds": [{
            "title": f"👻 Resumo de Sessão — {session['player']}",
            "color": 0x9B59B6,
            "fields": fields,
            "footer": {"text": f"Data da Sessão: {data_sessao}"}
        }]
    }

    response = requests.post(str(WEBHOOK_URL), json=payload, timeout=10)
    return response.status_code in (200, 204)

def send_loot_embed(session: dict) -> bool:
    horario_inicio, data_sessao = format_session_datetime(session)
    drops = [drop for drop in session.get("drops", []) if not drop.get("Ignored", False)]

    if not drops:
        raise ValueError("Esta sessão não possui drops válidos para enviar.")

    drops.sort(key=lambda drop: drop.get("Total price", 0), reverse=True)
    total_loot = sum(drop.get("Total price", 0) or 0 for drop in drops)

    loot_lines = []
    for drop in drops:
        item = drop.get("Item", "Item desconhecido")
        count = drop.get("Count", 0)
        total_price = drop.get("Total price", 0) or 0
        unit_price = drop.get("Unit price", 0) or 0
        loot_lines.append(
            f"• **{item}** × `{count:,}` — `{total_price:,.2f} gold` "
            f"(`{unit_price:,.2f}` cada)"
        )

    loot_text = "\n".join(loot_lines)
    if len(loot_text) > 3900:
        loot_text = loot_text[:3850] + "\n… (lista reduzida por limite de tamanho)"

    session_data = session.get("session_data", {})
    profit = session_data.get("Profit", 0) or 0
    supplies = session_data.get("Supplies", 0) or 0
    raw_gains = session_data.get("Raw gains", 0) or 0

    payload = {
        "embeds": [{
            "title": f"💰 Loots e Drops — {session['player']}",
            "color": 0x2ECC71,
            "description": loot_text,
            "fields": [
                {
                    "name": "📦 Total dos drops",
                    "value": f"`{total_loot:,.2f} gold`",
                    "inline": True
                },
                {
                    "name": "📈 Resultado da sessão",
                    "value": f"**Lucro:** `{profit:,.0f} gold`\n**Supplies:** `{supplies:,.0f} gold`\n**Raw gains:** `{raw_gains:,.0f} gold`",
                    "inline": True
                },
                {
                    "name": "🕒 Sessão",
                    "value": f"**Início:** `{horario_inicio}`\n**Duração:** `{format_duration(session['duration'])}`",
                    "inline": True
                }
            ],
            "footer": {"text": f"Data da sessão: {data_sessao} | Sessão {session['id']}"}
        }]
    }

    response = requests.post(str(WEBHOOK_URL), json=payload, timeout=10)
    return response.status_code in (200, 204)


# Tema visual: dark gamer, roxo e neon.
# CSS afeta somente a apresentação; não altera funções ou payloads do Discord.
st.markdown("""
<style>
    :root {
        --dash-bg: #09080f;
        --dash-panel: #12101d;
        --dash-panel-2: #191528;
        --dash-purple: #a855f7;
        --dash-cyan: #22d3ee;
        --dash-text: #f4f0ff;
        --dash-muted: #aaa2bd;
    }

    .stApp {
        background:
            radial-gradient(ellipse at 15% 0%, rgba(126, 34, 206, .16), transparent 38%),
            radial-gradient(ellipse at 90% 8%, rgba(34, 211, 238, .07), transparent 30%),
            var(--dash-bg);
        color: var(--dash-text);
    }

    [data-testid="stHeader"] { 
        background: #09080f;
        height: 3.5rem; 
    }
    [data-testid="stSidebar"] { background: #0d0b15; }

    .block-container {
        padding-top: 3.25rem;
        padding-bottom: 3rem;
        max-width: 1450px;
    }

    .dashboard-hero {
        padding: 1.35rem 1.5rem;
        margin-bottom: 1.3rem;
        border: 1px solid rgba(168, 85, 247, .42);
        border-left: 4px solid #a855f7;
        border-radius: 16px;
        background: linear-gradient(115deg, rgba(88, 28, 135, .27), rgba(18, 16, 29, .92) 58%, rgba(8, 145, 178, .10));
        box-shadow: 0 0 28px rgba(126, 34, 206, .09);
    }

    .dashboard-hero h1 {
        margin: 0;
        font-size: clamp(1.65rem, 3vw, 2.35rem);
        letter-spacing: -.04em;
        color: #faf5ff;
    }

    .dashboard-hero p {
        margin: .4rem 0 0;
        color: #bdb4d3;
        font-size: .95rem;
    }

    [data-testid="stExpander"] {
        border: 1px solid rgba(168, 85, 247, .27);
        border-radius: 14px;
        background: rgba(18, 16, 29, .76);
        overflow: hidden;
    }

    [data-testid="stExpander"] summary {
        background: rgba(88, 28, 135, .12);
    }

    [data-testid="stMetric"] {
        background: linear-gradient(145deg, rgba(31, 25, 48, .95), rgba(18, 16, 29, .95));
        border: 1px solid rgba(168, 85, 247, .28);
        border-radius: 13px;
        padding: 1rem 1.05rem;
        min-height: 112px;
        box-shadow: inset 0 1px 0 rgba(255,255,255,.025);
    }

    [data-testid="stMetricLabel"] {
        color: #bdb4d3 !important;
        font-size: .83rem;
    }

    [data-testid="stMetricValue"] {
        color: #f5eaff !important;
        font-weight: 750;
    }

    [data-testid="stDataFrame"], [data-testid="stTable"] {
        border: 1px solid rgba(168, 85, 247, .25);
        border-radius: 12px;
        overflow: hidden;
    }

    h2, h3 {
        letter-spacing: -.02em;
    }

    div.stButton > button {
        border-radius: 10px;
        border: 1px solid rgba(168, 85, 247, .45);
        transition: border-color .15s ease, transform .15s ease, box-shadow .15s ease;
    }

    div.stButton > button:hover {
        border-color: #c084fc;
        box-shadow: 0 0 15px rgba(168, 85, 247, .18);
        transform: translateY(-1px);
    }

    div.stButton > button[kind="primary"] {
        background: linear-gradient(100deg, #7e22ce, #9333ea);
        border: 1px solid #c084fc;
        color: white;
    }

    [data-testid="stTextArea"] textarea {
        background: #100d19;
        border-color: rgba(168, 85, 247, .35);
        border-radius: 12px;
    }

    hr { border-color: rgba(168, 85, 247, .22); }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="dashboard-hero">
    <h1>👻 Enemy Statistics Dashboard</h1>
    <p>Painel de sessões • desempenho, raros e histórico de caça</p>
</div>
""", unsafe_allow_html=True)

if "sessions" not in st.session_state:
    st.session_state.sessions = []

with st.container(border=True):
    st.subheader("➕ Adicionar sessão")
    st.caption("Cole o JSON copiado do jogo para registrar uma nova sessão.")
    json_text = st.text_area(
        "JSON da sessão",
        height=150,
        placeholder="Cole o JSON completo aqui (Ctrl+V)...",
        label_visibility="collapsed"
    )

    col_btn1, col_btn2, _ = st.columns([1.25, 1.0, 4])
    add_clicked = col_btn1.button("➕ Adicionar sessão", type="primary", width="stretch")
    clear_clicked = col_btn2.button("🗑️ Limpar tudo", width="stretch")

if clear_clicked:
    st.session_state.sessions = []
    st.rerun()

if add_clicked:
    if not json_text.strip():
        st.error("Cole algum JSON antes de adicionar.")
    else:
        try:
            new_session = parse_session_json(json_text)

            if any(s["id"] == new_session["id"] for s in st.session_state.sessions):
                st.warning(f"Sessão {new_session['id']} já foi adicionada.")
            else:
                st.session_state.sessions.append(new_session)
                st.success(
                    f"Sessão {new_session['id']} adicionada! "
                    f"Personagem: {new_session['player']} | "
                    f"{new_session['kills']} kills | "
                    f"{new_session['rares']} raros"
                )
                st.rerun()

        except json.JSONDecodeError:
            st.error("JSON inválido. Verifique se copiou o conteúdo completo.")
        except KeyError as e:
            st.error(f"JSON incompleto. Faltando a chave: {e}")

if not st.session_state.sessions:
    st.info("Nenhuma sessão registrada. Cole um JSON acima para começar.")
    st.stop()

st.divider()
st.subheader("🎮 Sessões registradas")
st.caption(f"{len(st.session_state.sessions)} sessão(ões) carregada(s) nesta visualização.")

for s in st.session_state.sessions:
    title = f"🎮 {s['player']}  ·  Sessão {s['id']}  ·  {s['kills']:,} kills  ·  {s['rares']} raros"

    with st.expander(title, expanded=True):
        df_s = process_enemies_dataframe(s["enemies"], s["kills"])
        rare_rates = calculate_rare_rates(df_s)

        # Cards de estatísticas: apresentação apenas, valores e cálculos originais.
        st.markdown("#### 📈 Desempenho da sessão")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("⚔️ Kills totais", f"{s['kills']:,}")
        col2.metric("✨ Raros", f"{s['rares']}")
        col3.metric("⚡ Kills por hora", f"{s['kills_h']:,}")
        col4.metric("🕒 Duração", s["duration"])

        st.markdown("#### ✨ Taxa de raros por espécie")
        if rare_rates:
            rate_cols = st.columns(min(3, len(rare_rates)))
            for index, rate in enumerate(rare_rates):
                label = "Shiny" if rate["tipo"] == "Shiny" else "Mega" if rate["tipo"] == "Mega" else "Raro"
                with rate_cols[index % len(rate_cols)]:
                    st.markdown(
                        f"""
                        <div style="
                            background: linear-gradient(135deg, rgba(88,28,135,.24), rgba(18,16,29,.9));
                            border: 1px solid rgba(168,85,247,.30);
                            border-radius: 12px;
                            padding: .85rem 1rem;
                            margin-bottom: .6rem;
                        ">
                            <div style="color:#c4b5fd;font-size:.78rem;">{label}</div>
                            <div style="font-size:1.05rem;font-weight:700;color:#faf5ff;">{rate['especie']}</div>
                            <div style="color:#a5f3fc;font-size:.9rem;">1 a cada {rate['taxa']:.0f}</div>
                            <div style="color:#aaa2bd;font-size:.76rem;">{rate['rare_count']} em {rate['total']} encontros</div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
        else:
            st.info("Nenhum raro nesta sessão.")

        st.markdown("#### 📋 Detalhamento de inimigos")
        df_display = df_s[["Enemy", "Count", "%", "Tipo"]].rename(
            columns={"Enemy": "Pokémon", "Count": "Quantidade", "%": "Participação", "Tipo": "Categoria"}
        )
        st.dataframe(
            df_display,
            width="stretch",
            hide_index=True,
            column_config={
                "Quantidade": st.column_config.NumberColumn(format="%d"),
                "Participação": st.column_config.NumberColumn(format="%.1f%%"),
            }
        )

        # Os dois botões e suas chamadas foram mantidos sem alteração.
        col_rare, col_loot = st.columns([1,1], gap="small")

        if col_rare.button("✨ Enviar raros ao Discord", key=f"send_rares_{s['id']}", width="stretch"):
            if not WEBHOOK_URL:
                st.error("Webhook não configurado nos Secrets / .env")
            else:
                try:
                    if send_rare_embed(s, df_s, rare_rates):
                        st.success("Resumo de raros enviado ao Discord!")
                    else:
                        st.error("Erro ao enviar a embed de raros.")
                except Exception as e:
                    st.error(f"Erro ao enviar raros: {e}")

        if col_loot.button("💰 Enviar loot/drops ao Discord", key=f"send_loot_{s['id']}", width="stretch"):
            if not WEBHOOK_URL:
                st.error("Webhook não configurado nos Secrets / .env")
            else:
                try:
                    if send_loot_embed(s):
                        st.success("Embed de loot/drops enviada ao Discord!")
                    else:
                        st.error("Erro ao enviar a embed de loot/drops.")
                except Exception as e:
                    st.error(f"Erro ao enviar loot/drops: {e}")