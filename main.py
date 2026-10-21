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

def send_to_discord(session: dict, df_s: pd.DataFrame, rare_rates: list[dict]) -> bool:
    try:
        dt_obj = datetime.strptime(session["start"], "%Y-%m-%d %H:%M:%S")
        horario_inicio = dt_obj.strftime("%H:%M")
        data_sessao = dt_obj.strftime("%d/%m/%Y")
    except ValueError:
        horario_inicio = session["start"]
        data_sessao = datetime.now().strftime("%d/%m/%Y")

    duracao_raw = session["duration"]
    try:
        partes = [int(p) for p in duracao_raw.split(":")]
        if len(partes) == 3:
            h, m, s = partes
            if h > 0:
                duracao_tratada = f"{h}h {m}min"
            elif m > 0:
                duracao_tratada = f"{m}min {s}s"
            else:
                duracao_tratada = f"{s}s"
        else:
            duracao_tratada = duracao_raw
    except Exception:
        duracao_tratada = duracao_raw

    normais_count = df_s[~df_s["Rare"]]["Count"].sum()
    raros_count = df_s[df_s["Rare"]]["Count"].sum()

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
                rates_text += f"• **{label} {r['especie']}** : _nenhum_\n"

        fields.append({
            "name": "✨ Taxa de Raros por Espécie",
            "value": rates_text.strip(),
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
        "value": monsters_table,
        "inline": False
    })

    payload = {
        "embeds": [
            {
                "title": f"👻 Resumo de Sessão — {session['player']}",
                "color": 0x9B59B6,
                "fields": fields,
                "footer": {
                    "text": f"Data da Sessão: {data_sessao}"
                }
            }
        ]
    }

    response = requests.post(str(WEBHOOK_URL), json=payload, timeout=10)
    return response.status_code in (200, 204)

# ====================== INTERFACE STREAMLIT ======================
st.title("👻 Enemy Statistics Dashboard")
st.caption("Cole o JSON direto da área de transferência (Ctrl+V)")

if "sessions" not in st.session_state:
    st.session_state.sessions = []

st.subheader("➕ Adicionar Sessão")
json_text = st.text_area(
    "Cole o JSON aqui (Ctrl+V)",
    height=180,
    placeholder="Cole o JSON completo que o jogo copiou..."
)

col_btn1, col_btn2, _ = st.columns([1, 1, 4])
add_clicked = col_btn1.button("Adicionar sessão", type="primary", width='stretch')
clear_clicked = col_btn2.button("Limpar tudo", width='stretch')

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
    st.info("Nenhuma sessão adicionada ainda. Cole um JSON acima.")
    st.stop()

st.divider()

for s in st.session_state.sessions:
    title = f"🎮 {s['player']} — Sessão {s['id']} | {s['start']} | {s['kills']} kills | {s['rares']} raros"

    with st.expander(title, expanded=True):
        df_s = process_enemies_dataframe(s["enemies"], s["kills"])
        rare_rates = calculate_rare_rates(df_s)

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Kills", f"{s['kills']:,}")
        col2.metric("Raros", f"{s['rares']}")
        col3.metric("Kills/h", f"{s['kills_h']}")
        col4.metric("Duração", s["duration"])

        st.subheader("✨ Taxa de Raros por Espécie")
        if rare_rates:
            for rate in rare_rates:
                label = "Shiny" if rate["tipo"] == "Shiny" else "Mega" if rate["tipo"] == "Mega" else "Raro"
                st.write(
                    f"**{rate['especie']}** ({label}): 1 a cada **{rate['taxa']:.0f}** "
                    f"({rate['rare_count']} {label.lower()}s em {rate['total']} kills)"
                )
        else:
            st.info("Nenhum raro nesta sessão.")

        df_display = df_s[["Enemy", "Count", "%", "Tipo"]].rename(
            columns={"Enemy": "Monstro", "Count": "Kills"}
        )
        st.dataframe(df_display, width='stretch', hide_index=True)

        if st.button(f"Enviar Sessão {s['id']} para o Discord", key=f"send_{s['id']}"):
            if not WEBHOOK_URL:
                st.error("Webhook não configurado nos Secrets / .env")
            else:
                try:
                    if send_to_discord(s, df_s, rare_rates):
                        st.success(f"Sessão {s['id']} enviada com sucesso!")
                    else:
                        st.error("Erro ao enviar a mensagem para o Discord.")
                except Exception as e:
                    st.error(f"Erro ao enviar: {e}")