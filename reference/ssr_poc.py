import os
import warnings
import numpy as np
import json
from datetime import datetime

import requests
import streamlit as st
from dotenv import load_dotenv
from langchain_openai import AzureChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIConnectionError

load_dotenv()
warnings.filterwarnings("ignore", message=r".*Returning '__path__' instead.*")

# ─── Defaults (overridable via .env) ─────────────────────────────────────────
DEFAULT_EMBEDDING_MODEL_ENDPOINT = "https://ai.questkart.cloud/embeddings"
DEFAULT_CHAT_API_VERSION      = "2024-02-15-preview"
DEFAULT_AZURE_CHAT_DEPLOYMENT = "gpt-4o-mini"

# ─── Page Config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="EPIC AI — SSR Concept Testing POC",
    page_icon="🧬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ─── JNJ Red Theme CSS ───────────────────────────────────────────────────────
st.markdown("""
<style>
    .stApp { background-color: #FDF5F5; }
    section[data-testid="stSidebar"] { background-color: #CC0000 !important; }
    section[data-testid="stSidebar"] * { color: white !important; }
    /* File card text — class selector beats * so these win */
    section[data-testid="stSidebar"] .fc-blue  { color: #185FA5 !important; }
    section[data-testid="stSidebar"] .fc-green { color: #2E7D32 !important; }
    section[data-testid="stSidebar"] .fc-sub   { color: #444444 !important; }
    section[data-testid="stSidebar"] .fc-red   { color: #CC0000 !important; }
    .jnj-header {
        background: linear-gradient(135deg, #CC0000, #A30000);
        color: white; padding: 20px 30px; border-radius: 8px; margin-bottom: 24px;
    }
    .jnj-header h1 { color: white; margin: 0; font-size: 28px; }
    .jnj-header p  { color: #FFD0D0; margin: 4px 0 0; font-size: 14px; }
    .card {
        background: white; border: 1px solid #E8C5C5;
        border-left: 4px solid #CC0000; border-radius: 8px;
        padding: 20px; margin-bottom: 16px;
        box-shadow: 0 2px 6px rgba(204,0,0,0.08);
    }
    .card h3 { color: #CC0000; margin-top: 0; font-size: 16px; }
    .section-header {
        background: #CC0000; color: white; padding: 10px 20px;
        border-radius: 6px; font-weight: bold; font-size: 15px; margin: 20px 0 12px;
    }
    .metric-box {
        background: white; border: 2px solid #CC0000;
        border-radius: 8px; padding: 16px; text-align: center;
    }
    .metric-box .value { font-size: 32px; font-weight: bold; color: #CC0000; }
    .metric-box .label { font-size: 12px; color: #666; margin-top: 4px; }
    .stProgress > div > div { background-color: #CC0000 !important; }
    .stButton > button {
        background-color: #CC0000; color: white;
        border: none; border-radius: 6px; font-weight: bold;
    }
    .stButton > button:hover { background-color: #A30000; }
    .stTabs [data-baseweb="tab"] { color: #CC0000; font-weight: 600; }
    .stTabs [aria-selected="true"] { border-bottom: 3px solid #CC0000; }
    .streamlit-expanderHeader { color: #CC0000 !important; }
    .quote-box {
        background: #FDF0F0; border-left: 4px solid #CC0000;
        padding: 12px 16px; border-radius: 0 6px 6px 0;
        font-style: italic; color: #444; margin: 8px 0; font-size: 13px;
    }
    .winner-badge {
        background: #CC0000; color: white; padding: 6px 16px;
        border-radius: 20px; font-weight: bold; font-size: 14px;
    }
    .insight-box {
        background: #FFF3CD; border: 1px solid #E07B2A;
        border-radius: 6px; padding: 12px 16px; margin: 8px 0; font-size: 13px;
    }
    .jnj-footer {
        text-align: center; color: #999; font-size: 12px;
        margin-top: 40px; padding-top: 20px; border-top: 1px solid #E8C5C5;
    }
</style>
""", unsafe_allow_html=True)

# ─── Pre-loaded Constants from Client Documents ───────────────────────────────
DEFAULT_CLAIMS = [
    "JNJ-5322 delivers CAR-T-like efficacy (>90% ORR, 28-month mPFS) as a fully outpatient therapy — no hospitalization, no REMS required",
    "Proven remission in triple-class exposed patients who have failed BCMA-targeted therapy — with no Grade 3+ CRS observed in the Phase 3 trial",
    "Fixed 18-month treatment duration with subcutaneous dosing — giving your patients certainty and your practice predictability",
    "Superior infection risk profile vs BCMA BsAb (Grade 3+: <25% vs 36-55%) with outpatient-manageable safety in community settings",
    "No ocular toxicity, no REMS, no boxed warning — a clean safety profile that lets you focus on efficacy, not monitoring burden"
]

DEFAULT_KBQ = "How compelling is this as a reason to prescribe JNJ-5322 (BCMAxCD3xGPRC5D tri-specific) to an eligible 4th-line RRMM patient? (1 = Extremely compelling, 5 = Not compelling at all)"

DEFAULT_ANCHORS = [
    "This message is extremely compelling — it directly addresses my biggest concern in 4L and would strongly influence my decision to prescribe JNJ-5322",
    "This is a persuasive message — it highlights a genuine clinical advantage and would make me more likely to consider JNJ-5322 for appropriate patients",
    "This message is somewhat relevant but neutral — it doesn't meaningfully change my view of JNJ-5322 compared to other 4L options",
    "This is a weak message for me — it touches on a minor factor or one I already knew, and wouldn't significantly influence my prescribing",
    "This message is not compelling at all — it either overstates the benefit, misses what I care about, or describes something I consider a disadvantage"
]

DEFAULT_PENALTIES = [
    {"trigger": "reimbursement",  "adjustment": 0.30, "reason": "Payer/reimbursement concern"},
    {"trigger": "IVIG",           "adjustment": 0.25, "reason": "IVIG operational burden"},
    {"trigger": "infection risk", "adjustment": 0.20, "reason": "Infection management concern"},
    {"trigger": "tocilizumab",    "adjustment": 0.25, "reason": "Tocilizumab logistics concern"},
    {"trigger": "CAR-T",          "adjustment": 0.15, "reason": "CAR-T preference over tri-specific"}
]

PERSONAS = {
    "Community Oncologist": """You are Dr. Sarah Chen, a Community Oncologist with 10 years experience in a suburban oncology practice. You treat approximately 80 multiple myeloma patients per year across all lines. You do NOT administer CAR-T — you refer those patients to academic centers. You've started using bispecifics but are early in the learning curve. You are sensitive to reimbursement issues, outpatient logistics, and infection management without hospital backup. Your typical 4L patient is older, has comorbidities, and limited support systems.""",

    "Academic Oncologist": """You are Dr. James Park, a Senior Academic Hematologist-Oncologist at a major cancer center with 15 years experience. You treat approximately 150 MM patients annually and personally administer CAR-T and bispecifics. You are data-driven, highly familiar with T-cell redirecting therapies, and comfortable managing CRS and infection risks. You focus on clinical evidence quality, mechanism of action, and sequencing strategy.""",

    "KOL": """You are Dr. Patricia Williams, a Senior Key Opinion Leader in multiple myeloma with over 25 years of experience. You lead multiple Phase 3 clinical trials. You are deeply familiar with the competitive landscape, trial design nuances, and long-term outcomes data. You think strategically about where a new therapy fits in the treatment algorithm and how it positions against CAR-T, bispecifics, and ADCs.""",

    "Payer/Medical Director": """You are a Medical Director at a large integrated delivery network. You evaluate new therapies for formulary placement and coverage decisions. You focus on comparative effectiveness, total cost of care, administrative burden of prior authorization, and budget impact. You are skeptical of novel agents without robust real-world data and head-to-head comparisons."""
}

# ─── SSR Math Functions ───────────────────────────────────────────────────────
def cosine_similarity(vec_a, vec_b):
    a, b = np.array(vec_a), np.array(vec_b)
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom > 0 else 0.0

def compute_pmf(response_embedding, anchor_embeddings, delta=0.02):
    similarities = [cosine_similarity(response_embedding, ae) for ae in anchor_embeddings]
    min_s = min(similarities)
    adjusted = [s - min_s + delta for s in similarities]
    total = sum(adjusted)
    pmf = [a / total for a in adjusted]
    mean_ssr = sum((k + 1) * p for k, p in enumerate(pmf))
    return {
        "similarities": [round(s, 4) for s in similarities],
        "adjusted":     [round(a, 4) for a in adjusted],
        "pmf":          [round(p, 4) for p in pmf],
        "mean_ssr":     round(mean_ssr, 2),
        "high_intent_pct": round((pmf[0] + pmf[1]) * 100, 1)
    }

def apply_penalties(mean_ssr, penalties, response_text):
    final_mean, triggered = mean_ssr, []
    for p in penalties:
        if p["trigger"].lower() in response_text.lower():
            final_mean += p["adjustment"]
            triggered.append(p)
    return round(final_mean, 2), triggered

def bradley_terry(n_claims, wins_matrix):
    strengths = np.ones(n_claims)
    for _ in range(500):
        new_s = np.zeros(n_claims)
        for i in range(n_claims):
            num = sum(wins_matrix[i][j] for j in range(n_claims) if j != i)
            den = sum(
                (wins_matrix[i][j] + wins_matrix[j][i]) / (strengths[i] + strengths[j])
                for j in range(n_claims)
                if j != i and (strengths[i] + strengths[j]) > 0
            )
            new_s[i] = num / den if den > 0 else 0
        total = sum(new_s)
        strengths = new_s / total if total > 0 else new_s
    return strengths

# ─── Clients (LangChain Azure Chat + Custom Embedding Endpoint) ──────────────
_chat_llm_client = None

@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(requests.exceptions.RequestException),
       reraise=True)
def _embed(texts: list) -> list:
    """POST to our custom embedding endpoint; returns one vector per input text."""
    url = os.getenv("EMBEDDING_MODEL_ENDPOINT", DEFAULT_EMBEDDING_MODEL_ENDPOINT)
    resp = requests.post(url, json={"texts": texts}, headers={"content-type": "application/json"}, timeout=30)
    resp.raise_for_status()
    return resp.json()["embeddings"]

def get_chat_llm(temperature: float = 0.4) -> AzureChatOpenAI:
    global _chat_llm_client
    if _chat_llm_client is None:
        azure_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        api_key        = os.environ["AZURE_OPENAI_API_KEY"].strip()
        api_version    = os.getenv("AZURE_OPENAI_API_VERSION", DEFAULT_CHAT_API_VERSION)
        deployment     = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", DEFAULT_AZURE_CHAT_DEPLOYMENT)
        _chat_llm_client = AzureChatOpenAI(
            azure_endpoint=azure_endpoint,
            api_version=api_version,
            azure_deployment=deployment,
            api_key=api_key,
            temperature=temperature,
            timeout=30.0,
            max_retries=3,
        )
    return _chat_llm_client

def _msg_text(message) -> str:
    c = message.content
    if isinstance(c, str): return c
    if isinstance(c, list):
        return "".join(p.get("text","") if isinstance(p, dict) else str(p) for p in c)
    return str(c)

@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=2, max=10),
       retry=retry_if_exception_type(APIConnectionError),
       reraise=True)
def call_llm(system_prompt: str, user_prompt: str, max_tokens: int = 800) -> str:
    """LLM call via Azure APIM (GPT-4.1)."""
    llm  = get_chat_llm()
    resp = llm.bind(max_tokens=max_tokens).invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ])
    return _msg_text(resp)

def precompute_anchor_embeddings(anchor_statements: list) -> list:
    """Embed all 5 anchor statements once before the simulation loop."""
    vectors = _embed(anchor_statements)
    if not vectors:
        raise RuntimeError("Embedding API returned no vectors.")
    return [np.array(v, dtype=float) for v in vectors]

def score_response_vs_anchors(response_text: str, anchor_vectors: list, delta: float = 0.02) -> dict:
    """Embed response → cosine similarity vs anchors → PMF → mean SSR."""
    rvec = np.array(_embed([response_text])[0], dtype=float)
    out  = compute_pmf(rvec, anchor_vectors, delta=delta)
    return {
        "similarities": out["similarities"],
        "pmf":          out["pmf"],
        "mean_ssr":     out["mean_ssr"],
        "high_intent":  (out["pmf"][0] + out["pmf"][1]) > 0.5,
    }

# ─── Core SSR Functions ───────────────────────────────────────────────────────
def generate_physician_response(persona_name: str, persona_desc: str,
                                claim_text: str, kbq: str) -> str:
    user = f"""You have been presented with this claim about JNJ-5322 (BCMAxCD3xGPRC5D tri-specific antibody for relapsed/refractory Multiple Myeloma):

CLAIM: "{claim_text}"

Key Belief Question: {kbq}

Respond in your own voice as this physician. Write 3-5 sentences giving your genuine clinical perspective on how compelling this message is and why. Be specific about what resonates or doesn't resonate based on your practice setting and experience. Do NOT give a numeric rating — write as if you are in a market research interview."""
    return call_llm(persona_desc, user, max_tokens=300)

def generate_executive_summary(rankings, cohort_results, claims, kbq, n_respondents) -> str:
    ranked_text  = "\n".join([
        f"Rank {r['rank']}: \"{r['claim_text'][:80]}...\" — BT Score: {r['bt_score']:.3f}, Win Rate: {r['win_rate']:.0f}%"
        for r in rankings
    ])
    cohort_text  = json.dumps(cohort_results, indent=2)
    prompt = f"""Generate a professional SSR Concept Testing executive summary for a pharmaceutical market research report.

Study Context:
- Drug: JNJ-5322 (BCMAxCD3xGPRC5D tri-specific) for 4th-line RRMM
- KBQ: {kbq}
- Total synthetic respondents: {n_respondents}
- Claims tested: {len(claims)}

CLAIM RANKINGS (Bradley-Terry tournament results):
{ranked_text}

COHORT BREAKDOWN:
{cohort_text}

Write with:
1. One-sentence study objective
2. 3-4 headline findings with "So what?" implications (start each with "•")
3. Key insight about the winning vs losing messages
4. One strategic recommendation for the launch team

Use professional market research language. Be specific with the numbers. Keep it concise."""
    return call_llm("You are a senior pharmaceutical market research analyst writing an executive summary.", prompt, max_tokens=800)

def generate_interpretation(rankings, claims, personas_used) -> str:
    winner, loser = rankings[0], rankings[-1]
    prompt = f"""Write a detailed interpretation section for an SSR Concept Testing study on JNJ-5322 messaging.

WINNING CLAIM (Rank 1): "{winner['claim_text']}"
- Bradley-Terry Score: {winner['bt_score']:.3f}
- Win Rate: {winner['win_rate']:.0f}%

LOSING CLAIM (Rank {loser['rank']}): "{loser['claim_text']}"
- Bradley-Terry Score: {loser['bt_score']:.3f}
- Win Rate: {loser['win_rate']:.0f}%

PERSONAS: {', '.join(personas_used)}

Write 3 paragraphs:
1. Why the winning claim resonated across physician personas
2. Why the losing claim underperformed
3. Differentiation by physician type (community vs academic vs KOL perspective)

Use clinical language appropriate for oncology market research."""
    return call_llm("You are a pharmaceutical market research analyst.", prompt, max_tokens=600)

# ─── UI Helpers ───────────────────────────────────────────────────────────────
def render_header():
    st.markdown("""
    <div class="jnj-header">
        <h1>🧬 EPIC AI — SSR Concept Testing POC</h1>
        <p>J&J Innovative Medicine · Business Technology · Synthetic Respondent Market Research Simulation</p>
    </div>
    """, unsafe_allow_html=True)

def render_ranking_card(rank_data, rank_num):
    styles = {1: ("🥇","#CC0000","#FFF0F0"), 2: ("🥈","#E07B2A","#FFF8F0"), 3: ("🥉","#64748B","#F8F8F8")}
    emoji, color, bg = styles.get(rank_num, ("","#333","#FFF"))
    st.markdown(f"""
    <div style="background:{bg};border:1px solid #E8C5C5;border-left:5px solid {color};
                border-radius:8px;padding:16px;margin-bottom:12px;">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-size:20px;font-weight:bold;color:{color};">{emoji} Rank {rank_num}</span>
            <span style="background:{color};color:white;padding:4px 12px;border-radius:20px;font-size:13px;font-weight:bold;">
                BT Score: {rank_data['bt_score']:.3f}
            </span>
        </div>
        <p style="margin:10px 0 6px;font-size:14px;color:#333;font-style:italic;">"{rank_data['claim_text']}"</p>
        <div style="display:flex;gap:20px;margin-top:8px;">
            <span style="font-size:12px;color:#666;">✅ Win Rate: <strong>{rank_data['win_rate']:.0f}%</strong></span>
            <span style="font-size:12px;color:#666;">📊 Mean SSR: <strong>{rank_data['mean_ssr']:.2f}</strong></span>
            <span style="font-size:12px;color:#666;">👥 High Intent: <strong>{rank_data['high_intent_pct']:.0f}%</strong></span>
        </div>
    </div>
    """, unsafe_allow_html=True)

# ─── Env hydration from Streamlit secrets ─────────────────────────────────────
def _hydrate_env():
    try:
        sec  = st.secrets
        keys = ("AZURE_OPENAI_API_KEY","AZURE_OPENAI_ENDPOINT",
                "AZURE_OPENAI_DEPLOYMENT_NAME","AZURE_OPENAI_API_VERSION",
                "EMBEDDING_MODEL_ENDPOINT")
        for k in keys:
            if k in sec and not os.environ.get(k):
                os.environ[k] = sec[k] if isinstance(sec[k], str) else str(sec[k])
    except Exception:
        pass

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    _hydrate_env()
    render_header()

    # ── Sidebar ──────────────────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## 📋 Study Setup Wizard")
        st.markdown("---")

        # Step 1 — Mode (Concept Testing only, no Q&A)
        st.markdown("### Step 1 — Study Mode")
        st.markdown("""
        <div style="background:white;border-radius:6px;padding:10px 12px;margin-bottom:4px;">
            <div style="display:flex;align-items:center;gap:8px;">
                <span style="font-size:16px;">🧪</span>
                <div>
                    <div class="fc-red" style="font-size:13px;font-weight:700;">
                        Concept Testing (Pairwise)
                    </div>
                    <div class="fc-sub" style="font-size:11px;margin-top:2px;">
                        Claims tested head-to-head · Bradley-Terry ranking · SSR simulation
                    </div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("---")

        # Step 2 — Hardcoded file cards
        st.markdown("### Step 2 — Uploaded Study Files")
        st.markdown("""
        <div style="background:white;border-radius:8px;padding:10px 12px;margin-bottom:6px;">
            <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:8px;">
                <span style="font-size:18px;margin-top:2px;">📊</span>
                <div style="flex:1;">
                    <div class="fc-blue" style="font-size:11px;font-weight:700;">
                        TPP_s_for_Testing_Stimuli.pptx
                    </div>
                    <div class="fc-sub" style="font-size:10px;margin-top:1px;">
                        Drug profile · 4L &amp; 2L-3L scenarios · Claims extracted
                    </div>
                </div>
                <span style="background:#1E7A4A;color:white;font-size:9px;font-weight:700;
                             border-radius:3px;padding:2px 6px;white-space:nowrap;margin-top:2px;">✓ Loaded</span>
            </div>
            <div style="border-top:1px solid #F0F0F0;padding-top:8px;display:flex;align-items:flex-start;gap:8px;">
                <span style="font-size:18px;margin-top:2px;">📝</span>
                <div style="flex:1;">
                    <div class="fc-green" style="font-size:11px;font-weight:700;">
                        Tri-Specific_TPP_Research_Discussion_Guide.docx
                    </div>
                    <div class="fc-sub" style="font-size:10px;margin-top:1px;">
                        KBQ questions · ZS Associates guide · 4L &amp; 2L+ questions
                    </div>
                </div>
                <span style="background:#1E7A4A;color:white;font-size:9px;font-weight:700;
                             border-radius:3px;padding:2px 6px;white-space:nowrap;margin-top:2px;">✓ Loaded</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        st.caption("📌 Production: user uploads files → system auto-extracts claims + KBQs")

        st.markdown("---")

        # Step 3 — Personas
        st.markdown("### Step 3 — Personas & Cohort")
        selected_personas = st.multiselect(
            "Select Personas", list(PERSONAS.keys()),
            default=["Community Oncologist", "Academic Oncologist"]
        )
        n_per_persona = st.slider("Synthetic respondents per persona", 1, 5, 2)

        st.markdown("---")

        # Step 4 — Claims slider
        st.markdown("### Step 4 — Number of Claims")
        n_claims_to_use = st.slider("Claims to test", 2, 5, 3,
                                     help="Start with 2-3 for fast POC demo")

        st.markdown("---")

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs([
        "📝 Configure Study", "▶ Run Simulation", "📊 Results Report", "🔍 Audit Package"
    ])

    # ═══ TAB 1: CONFIGURE ════════════════════════════════════════════════════
    with tab1:
        st.markdown('<div class="section-header">Step 5 — Claims to Test (Upload Stimuli)</div>',
                    unsafe_allow_html=True)
        st.caption("From client document: TPP's for Testing_Stimuli.pptx — JNJ-5322 4L messaging options")

        claims = []
        for i in range(n_claims_to_use):
            default = DEFAULT_CLAIMS[i] if i < len(DEFAULT_CLAIMS) else ""
            claim   = st.text_area(f"Claim {i+1}", value=default, height=80, key=f"claim_{i}")
            if claim.strip():
                claims.append(claim.strip())

        st.markdown('<div class="section-header">Step 6 — Discussion Guide (KBQ)</div>',
                    unsafe_allow_html=True)
        st.caption("From client document: Tri-Specific_TPP_Research_Discussion_Guide.docx")
        kbq = st.text_area("Key Belief Question", value=DEFAULT_KBQ, height=80)

        st.markdown('<div class="section-header">Step 7 — Anchor Statements (LLM-Generated, Editable)</div>',
                    unsafe_allow_html=True)
        st.caption("Auto-generated from the KBQ. User reviews and edits before running.")
        anchors = []
        for i in range(5):
            anchor = st.text_input(f"Anchor {i+1} (Rating {i+1})", value=DEFAULT_ANCHORS[i], key=f"anchor_{i}")
            anchors.append(anchor)

        st.markdown('<div class="section-header">Step 8 — Penalties (LLM-Generated, Editable)</div>',
                    unsafe_allow_html=True)
        st.caption("Real-world friction adjustments applied post-SSR. Modify as needed.")
        col1, col2, col3 = st.columns([2, 1, 3])
        col1.caption("Trigger phrase"); col2.caption("Adjustment"); col3.caption("Reason")
        penalties = []
        for i, p in enumerate(DEFAULT_PENALTIES):
            c1, c2, c3 = st.columns([2, 1, 3])
            trigger = c1.text_input("", value=p["trigger"], key=f"p_trigger_{i}", label_visibility="collapsed")
            adj     = c2.number_input("", value=float(p["adjustment"]), step=0.05, key=f"p_adj_{i}", label_visibility="collapsed")
            reason  = c3.text_input("", value=p["reason"], key=f"p_reason_{i}", label_visibility="collapsed")
            penalties.append({"trigger": trigger, "adjustment": adj, "reason": reason})

        st.session_state["study_config"] = {
            "claims": claims, "kbq": kbq, "anchors": anchors, "penalties": penalties,
            "personas": selected_personas, "n_per_persona": n_per_persona,
            "studies": ["TPP_s_for_Testing_Stimuli.pptx",
                        "Tri-Specific_TPP_Research_Discussion_Guide.docx"]
        }
        st.success(f"✅ Study configured: {len(claims)} claims, {len(selected_personas)} personas, "
                   f"{len(selected_personas)*n_per_persona} total respondents")

    # ═══ TAB 2: RUN ══════════════════════════════════════════════════════════
    with tab2:
        st.markdown('<div class="section-header">Run SSR Concept Testing Study</div>',
                    unsafe_allow_html=True)

        config      = st.session_state.get("study_config", {})
        claims      = config.get("claims", [])
        kbq         = config.get("kbq", DEFAULT_KBQ)
        anchors     = config.get("anchors", DEFAULT_ANCHORS)
        penalties   = config.get("penalties", DEFAULT_PENALTIES)
        personas    = config.get("personas", ["Community Oncologist"])
        n_per_persona = config.get("n_per_persona", 2)

        if len(claims) < 2:
            st.warning("Please configure at least 2 claims in the Configure tab.")
            return

        n_pairs      = len(claims) * (len(claims) - 1) // 2
        n_respondents = len(personas) * n_per_persona

        col1, col2, col3 = st.columns(3)
        col1.markdown(f'<div class="metric-box"><div class="value">{len(claims)}</div><div class="label">Claims to Test</div></div>', unsafe_allow_html=True)
        col2.markdown(f'<div class="metric-box"><div class="value">{n_pairs}</div><div class="label">Pairwise Matchups</div></div>', unsafe_allow_html=True)
        col3.markdown(f'<div class="metric-box"><div class="value">{n_respondents}</div><div class="label">Synthetic Respondents</div></div>', unsafe_allow_html=True)

        st.markdown("")

        if st.button("▶ Run Study", type="primary", use_container_width=True):
            # Validate Azure config
            try:
                anchor_vecs = precompute_anchor_embeddings(anchors)
            except Exception as e:
                st.error(f"Embedding error — check EMBEDDING_MODEL_ENDPOINT. Detail: {e}")
                return
            try:
                get_chat_llm()
            except Exception as e:
                st.error(f"Chat LLM error — check AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY. Detail: {e}")
                return

            all_results  = {}
            progress_bar = st.progress(0)
            status_box   = st.empty()
            live_log     = st.empty()
            log_lines    = []
            step         = 0
            total_steps  = n_pairs * len(personas) * n_per_persona

            pair_indices = [(i, j) for i in range(len(claims)) for j in range(i+1, len(claims))]
            wins         = [[0.0]*len(claims) for _ in range(len(claims))]

            for pi, (i, j) in enumerate(pair_indices):
                pair_key = f"{i}_{j}"
                all_results[pair_key] = {
                    "claim_a_idx": i, "claim_b_idx": j,
                    "claim_a_text": claims[i], "claim_b_text": claims[j],
                    "respondents": []
                }

                for persona_name in personas:
                    persona_desc = PERSONAS[persona_name]

                    for rep in range(n_per_persona):
                        step += 1
                        progress_bar.progress(step / total_steps)
                        status_box.markdown(
                            f'<div class="card"><b>🔄 Pair {pi+1}/{n_pairs}:</b> '
                            f'Claim {i+1} vs Claim {j+1} · <b>{persona_name}</b> #{rep+1}</div>',
                            unsafe_allow_html=True
                        )

                        # Score Claim A
                        resp_a   = generate_physician_response(persona_name, persona_desc, claims[i], kbq)
                        out_a    = score_response_vs_anchors(resp_a, anchor_vecs)
                        mean_a   = float(out_a["mean_ssr"])
                        final_a, pen_a = apply_penalties(mean_a, penalties, resp_a)

                        # Score Claim B
                        resp_b   = generate_physician_response(persona_name, persona_desc, claims[j], kbq)
                        out_b    = score_response_vs_anchors(resp_b, anchor_vecs)
                        mean_b   = float(out_b["mean_ssr"])
                        final_b, pen_b = apply_penalties(mean_b, penalties, resp_b)

                        winner_label = "A" if final_a < final_b else "B"
                        wins[i][j] += (1 if winner_label == "A" else 0)
                        wins[j][i] += (1 if winner_label == "B" else 0)

                        all_results[pair_key]["respondents"].append({
                            "persona": persona_name, "rep": rep+1,
                            "claim_a": {
                                "text": claims[i], "response": resp_a,
                                "similarities": [round(s,3) for s in out_a["similarities"]],
                                "pmf": [round(p,4) for p in out_a["pmf"]],
                                "base_mean": round(mean_a,2), "final_mean": final_a,
                                "penalties": [p["reason"] for p in pen_a]
                            },
                            "claim_b": {
                                "text": claims[j], "response": resp_b,
                                "similarities": [round(s,3) for s in out_b["similarities"]],
                                "pmf": [round(p,4) for p in out_b["pmf"]],
                                "base_mean": round(mean_b,2), "final_mean": final_b,
                                "penalties": [p["reason"] for p in pen_b]
                            },
                            "winner": winner_label
                        })

                        log_lines.append(
                            f"✅ Pair {i+1}v{j+1} | {persona_name} #{rep+1} | "
                            f"A={final_a:.2f} B={final_b:.2f} → Winner: Claim **{winner_label}**"
                        )
                        live_log.markdown("\n\n".join(log_lines[-6:]))

            progress_bar.progress(1.0)
            status_box.success("✅ All matchups complete! Computing rankings...")

            # Bradley-Terry
            strengths = bradley_terry(len(claims), wins)
            tw_list   = [sum(wins[i]) for i in range(len(claims))]
            tl_list   = [sum(wins[j][i] for j in range(len(claims))) for i in range(len(claims))]

            # Per-claim mean SSR + high intent
            claim_means, claim_hi = [], []
            for ci in range(len(claims)):
                all_pmfs = []
                for pk, pdata in all_results.items():
                    for resp in pdata["respondents"]:
                        if pdata["claim_a_idx"] == ci: all_pmfs.append(resp["claim_a"]["pmf"])
                        elif pdata["claim_b_idx"] == ci: all_pmfs.append(resp["claim_b"]["pmf"])
                if all_pmfs:
                    avg = [sum(p[k] for p in all_pmfs)/len(all_pmfs) for k in range(5)]
                    claim_means.append(round(sum((k+1)*p for k,p in enumerate(avg)), 2))
                    claim_hi.append(round((avg[0]+avg[1])*100, 1))
                else:
                    claim_means.append(3.0); claim_hi.append(33.0)

            ranked_indices = sorted(range(len(claims)), key=lambda x: strengths[x], reverse=True)
            rankings = []
            for rank, ci in enumerate(ranked_indices):
                tw, tl = tw_list[ci], tl_list[ci]
                rankings.append({
                    "rank": rank+1, "claim_idx": ci, "claim_text": claims[ci],
                    "bt_score": round(float(strengths[ci]), 3),
                    "win_rate": round(tw/(tw+tl)*100 if (tw+tl) else 0, 1),
                    "mean_ssr": claim_means[ci], "high_intent_pct": claim_hi[ci]
                })

            # Cohort breakdown
            cohort_results = {}
            for persona_name in personas:
                pw = {ci: 0 for ci in range(len(claims))}
                pt = {ci: 0 for ci in range(len(claims))}
                for pk, pdata in all_results.items():
                    for resp in pdata["respondents"]:
                        if resp["persona"] != persona_name: continue
                        ca, cb = pdata["claim_a_idx"], pdata["claim_b_idx"]
                        pt[ca] += 1; pt[cb] += 1
                        if resp["winner"] == "A": pw[ca] += 1
                        else: pw[cb] += 1
                cohort_results[persona_name] = {
                    ci: {"win_rate": round(pw[ci]/pt[ci]*100,1) if pt[ci] else 0, "preference_rank": 0}
                    for ci in range(len(claims))
                }
                sorted_ci = sorted(range(len(claims)),
                                   key=lambda x: cohort_results[persona_name][x]["win_rate"], reverse=True)
                for rank, ci in enumerate(sorted_ci):
                    cohort_results[persona_name][ci]["preference_rank"] = rank + 1

            status_box.info("📝 Generating executive summary...")
            exec_summary    = generate_executive_summary(rankings, cohort_results, claims, kbq, n_respondents)
            interpretation  = generate_interpretation(rankings, claims, personas)

            st.session_state["study_results"] = {
                "rankings": rankings, "all_results": all_results,
                "cohort_results": cohort_results,
                "exec_summary": exec_summary, "interpretation": interpretation,
                "claims": claims, "kbq": kbq, "anchors": anchors,
                "penalties": penalties, "personas": personas,
                "n_respondents": n_respondents,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "wins_matrix": wins
            }
            status_box.success("✅ Study complete! View results in the Results Report tab.")
            st.balloons()

    # ═══ TAB 3: RESULTS ══════════════════════════════════════════════════════
    with tab3:
        results = st.session_state.get("study_results")
        if not results:
            st.info("Run the study first to see results here.")
            return

        rankings      = results["rankings"]
        claims        = results["claims"]
        kbq           = results["kbq"]
        anchors       = results["anchors"]
        cohort_results= results["cohort_results"]
        all_results   = results["all_results"]

        st.markdown(f"""
        <div style="background:#FFF0F0;border:1px solid #E8C5C5;border-radius:8px;padding:16px;margin-bottom:20px;">
            <h3 style="color:#CC0000;margin:0;">SSR Concept Testing — Simulation Report</h3>
            <p style="color:#666;margin:4px 0;">Illustrative synthetic output · For Internal Use Only · J&J Innovative Medicine</p>
            <p style="color:#888;margin:0;font-size:12px;">
                Generated: {results['timestamp']} · Respondents: {results['n_respondents']} synthetic ·
                Claims tested: {len(claims)} · KBQ: {kbq[:80]}...
            </p>
        </div>
        """, unsafe_allow_html=True)

        # 1. Executive Summary
        st.markdown('<div class="section-header">1. Executive Summary</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card"><p style="margin:0;white-space:pre-wrap;">{results["exec_summary"]}</p></div>',
                    unsafe_allow_html=True)

        # 2. Rankings
        st.markdown('<div class="section-header">2. Claim Rankings — Bradley-Terry Tournament Results</div>',
                    unsafe_allow_html=True)
        st.caption("Bradley-Terry model — accounts for strength of opponents beaten, not just raw win count.")
        for r in rankings:
            render_ranking_card(r, r["rank"])

        st.markdown("**Win Rate Summary:**")
        cols = st.columns([3] + [1]*len(claims))
        cols[0].markdown("**Claim**")
        for ci in range(len(claims)):
            cols[ci+1].markdown(f"**vs C{ci+1}**")
        for r in rankings:
            ci = r["claim_idx"]
            row_cols = st.columns([3] + [1]*len(claims))
            row_cols[0].caption(f"C{ci+1}: {claims[ci][:45]}...")
            for cj in range(len(claims)):
                if ci == cj:
                    row_cols[cj+1].caption("—")
                else:
                    w = results["wins_matrix"][ci][cj]
                    l = results["wins_matrix"][cj][ci]
                    color = "green" if w > l else "red"
                    row_cols[cj+1].markdown(f"<span style='color:{color}'>{w:.0f}W/{l:.0f}L</span>",
                                            unsafe_allow_html=True)

        # 3. Cohort Breakdown
        st.markdown('<div class="section-header">3. Cohort Breakdown by Physician Type</div>', unsafe_allow_html=True)
        for persona_name, persona_data in cohort_results.items():
            with st.expander(f"👩‍⚕️ {persona_name} Preferences", expanded=True):
                sorted_claims = sorted(persona_data.items(), key=lambda x: x[1]["win_rate"], reverse=True)
                for ci, data in sorted_claims:
                    rank = data["preference_rank"]
                    wr   = data["win_rate"]
                    color = "#CC0000" if rank == 1 else "#E07B2A" if rank == 2 else "#64748B"
                    st.markdown(
                        f'<div style="padding:8px;margin:4px 0;background:#FFF;border-left:4px solid {color};border-radius:4px;">'
                        f'<b>#{rank}</b> — C{ci+1}: {claims[ci][:60]}... '
                        f'<span style="float:right;color:{color};">Win Rate: {wr:.0f}%</span></div>',
                        unsafe_allow_html=True
                    )

        top_picks   = {p: min(d.items(), key=lambda x: x[1]["preference_rank"])[0] for p, d in cohort_results.items()}
        unique_picks = set(top_picks.values())
        st.markdown("**⚠️ Stakeholder Alignment Check:**")
        if len(unique_picks) == 1:
            st.success(f"✅ All personas agree — Claim {list(unique_picks)[0]+1} is the preferred message.")
        else:
            st.warning("⚠️ Personas disagree: " +
                       " | ".join([f"{p}: C{ci+1}" for p, ci in top_picks.items()]) +
                       " — Consider differentiated messaging by audience.")

        # 4. Interpretation
        st.markdown('<div class="section-header">4. Detailed Interpretation</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="card"><p style="margin:0;white-space:pre-wrap;">{results["interpretation"]}</p></div>',
                    unsafe_allow_html=True)

        # 5. Sample Responses
        st.markdown('<div class="section-header">5. Sample Synthetic Respondent Responses</div>', unsafe_allow_html=True)
        st.caption("Showing one respondent per matchup. All responses stored in audit package.")
        for pk, pdata in list(all_results.items())[:2]:
            ca, cb = pdata["claim_a_idx"], pdata["claim_b_idx"]
            if pdata["respondents"]:
                resp = pdata["respondents"][0]
                with st.expander(f"Pair: C{ca+1} vs C{cb+1} — {resp['persona']}"):
                    c1, c2 = st.columns(2)
                    for col, side in [(c1,"claim_a"),(c2,"claim_b")]:
                        with col:
                            ci_label = ca+1 if side == "claim_a" else cb+1
                            st.markdown(f"**Claim {ci_label} Response:**")
                            st.markdown(f'<div class="quote-box">{resp[side]["response"]}</div>', unsafe_allow_html=True)
                            st.caption(f"Base SSR: {resp[side]['base_mean']} | Final SSR: {resp[side]['final_mean']} | Penalties: {resp[side]['penalties'] or 'None'}")
                            st.markdown("**PMF Distribution:**")
                            for k, (p, label) in enumerate(zip(resp[side]["pmf"],
                                    ["Extremely compelling","Persuasive","Neutral","Weak","Not compelling"])):
                                color = "#CC0000" if k < 2 else "#64748B"
                                st.markdown(
                                    f'<div style="margin:2px 0;">'
                                    f'<span style="font-size:11px;display:inline-block;width:140px;">{label}</span>'
                                    f'<div style="display:inline-block;background:{color};height:14px;width:{int(p*200)}px;border-radius:3px;vertical-align:middle;"></div>'
                                    f'<span style="font-size:11px;margin-left:6px;">{p*100:.1f}%</span>'
                                    f'</div>', unsafe_allow_html=True
                                )
                    winner_ci = ca if resp["winner"] == "A" else cb
                    st.markdown(f'<div style="text-align:center;margin-top:10px;"><span class="winner-badge">✓ Winner: Claim {winner_ci+1}</span></div>',
                                unsafe_allow_html=True)

        # 6. Penalty Impact
        st.markdown('<div class="section-header">6. Penalty Impact Summary (Base vs Final SSR)</div>', unsafe_allow_html=True)
        penalty_hits = {}
        for pdata in all_results.values():
            for resp in pdata["respondents"]:
                for pen in resp["claim_a"]["penalties"] + resp["claim_b"]["penalties"]:
                    penalty_hits[pen] = penalty_hits.get(pen, 0) + 1
        if penalty_hits:
            st.markdown("**Penalties triggered across all respondents:**")
            for reason, count in sorted(penalty_hits.items(), key=lambda x: x[1], reverse=True):
                pct = count / (results["n_respondents"] * len(claims)) * 100
                st.markdown(
                    f'<div style="padding:6px 12px;margin:4px 0;background:#FFF8F0;border-left:3px solid #E07B2A;border-radius:4px;">'
                    f'⚠️ <b>{reason}</b> — triggered {count} times ({pct:.0f}% of responses)</div>',
                    unsafe_allow_html=True
                )
        else:
            st.info("No penalties were triggered in this simulation run.")

        # 7. Strategic Recommendation
        st.markdown('<div class="section-header">7. Strategic Recommendation</div>', unsafe_allow_html=True)
        winner = rankings[0]
        st.markdown(f"""
        <div class="insight-box">
        <b>💡 Action Item:</b> Lead your detail aid and sales rep messaging with
        <b>Claim {winner['claim_idx']+1}</b> — this won {winner['win_rate']:.0f}% of all head-to-head matchups
        across {results['n_respondents']} synthetic oncologists with a Bradley-Terry score of {winner['bt_score']:.3f}.<br><br>
        <b>Before human quant:</b> Use these results to prioritize which 1-2 messages go into expensive human market research.
        Don't test all {len(claims)} — test the winner against your next best alternative.
        </div>
        """, unsafe_allow_html=True)

    # ═══ TAB 4: AUDIT ════════════════════════════════════════════════════════
    with tab4:
        results = st.session_state.get("study_results")
        if not results:
            st.info("Run the study first to see the audit package.")
            return

        st.markdown('<div class="section-header">Audit & Technical Package</div>', unsafe_allow_html=True)
        st.markdown("**8.1 Run Configuration:**")
        st.markdown(f"""<div class="card"><h3>Study Parameters</h3>
        <b>Claims tested:</b> {len(results['claims'])}<br>
        <b>Pairs evaluated:</b> {len(results['all_results'])}<br>
        <b>Personas:</b> {', '.join(results['personas'])}<br>
        <b>Respondents:</b> {results['n_respondents']}<br>
        <b>Anchor version:</b> v1.0</div>""", unsafe_allow_html=True)

        st.markdown("**8.2 Anchor Statement Library (Version 1.0):**")
        for i, anchor in enumerate(results["anchors"]):
            st.markdown(
                f'<div style="padding:8px 12px;margin:4px 0;background:#FFF;border-left:3px solid #CC0000;border-radius:4px;font-size:13px;">'
                f'<b>Anchor {i+1} (Rating {i+1}):</b> {anchor}</div>',
                unsafe_allow_html=True
            )

        st.markdown("**8.3 Worked Example Calculation:**")
        first_pair = list(results["all_results"].values())[0]
        if first_pair["respondents"]:
            resp = first_pair["respondents"][0]
            ci_a = first_pair["claim_a_idx"]
            with st.expander("Show full step-by-step calculation", expanded=True):
                st.markdown(f"**Claim evaluated:** C{ci_a+1}: *\"{results['claims'][ci_a][:70]}...\"*")
                st.markdown(f"**Persona:** {resp['persona']}")
                st.markdown(f'<div class="quote-box">{resp["claim_a"]["response"]}</div>', unsafe_allow_html=True)
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown("**Step 1 — Cosine Similarities:**")
                    for k, s in enumerate(resp["claim_a"]["similarities"]):
                        st.markdown(f"s{k+1} (anchor {k+1}) = `{s}`")
                with c2:
                    st.markdown("**Step 2 — Adjusted (−min + δ):**")
                    sims = resp["claim_a"]["similarities"]
                    min_s = min(sims)
                    for k, s in enumerate(sims):
                        st.markdown(f"a{k+1} = `{round(s - min_s + 0.02, 4)}`")
                    st.markdown(f"Σa = `{sum(round(s-min_s+0.02,4) for s in sims):.4f}`")
                with c3:
                    st.markdown("**Step 3 — PMF (normalized):**")
                    for k, p in enumerate(resp["claim_a"]["pmf"]):
                        st.markdown(f"p{k+1} = `{p}` ({p*100:.1f}%)")
                st.markdown(f"""
                **Step 4 — Mean SSR:**
                μ = 1({resp['claim_a']['pmf'][0]}) + 2({resp['claim_a']['pmf'][1]}) +
                3({resp['claim_a']['pmf'][2]}) + 4({resp['claim_a']['pmf'][3]}) +
                5({resp['claim_a']['pmf'][4]}) = **{resp['claim_a']['base_mean']}**

                **Step 5 — After Penalties:**
                Penalties triggered: {resp['claim_a']['penalties'] or 'None'}
                Final SSR = **{resp['claim_a']['final_mean']}**
                """)

        st.markdown("**8.4 Full Raw Data (All Respondent Records):**")
        with st.expander("View complete dataset (JSON)"):
            st.json({
                "study_config": {"kbq": results["kbq"], "claims": results["claims"],
                                 "anchors": results["anchors"], "penalties": results["penalties"],
                                 "personas": results["personas"], "timestamp": results["timestamp"]},
                "rankings": results["rankings"],
                "cohort_results": {p: {str(ci): d for ci, d in cd.items()}
                                   for p, cd in results["cohort_results"].items()},
                "all_respondent_data": {
                    pk: {"claim_a_text": pd["claim_a_text"], "claim_b_text": pd["claim_b_text"],
                         "n_respondents": len(pd["respondents"]),
                         "a_wins": sum(1 for r in pd["respondents"] if r["winner"]=="A"),
                         "b_wins": sum(1 for r in pd["respondents"] if r["winner"]=="B")}
                    for pk, pd in results["all_results"].items()
                }
            })

        st.markdown("**Export Report:**")
        report_json = json.dumps({
            "title": "EPIC AI SSR Concept Testing Report",
            "generated": results["timestamp"],
            "executive_summary": results["exec_summary"],
            "rankings": results["rankings"],
            "interpretation": results["interpretation"],
            "audit": {
                "model": os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", DEFAULT_AZURE_CHAT_DEPLOYMENT),
                "embedding": os.getenv("EMBEDDING_MODEL_ENDPOINT", DEFAULT_EMBEDDING_MODEL_ENDPOINT),
                "delta": 0.02,
                "anchors": results["anchors"],
                "penalties": results["penalties"]
            }
        }, indent=2)
        st.download_button(
            "📥 Download Full Report (JSON)", data=report_json,
            file_name=f"ssr_report_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json", use_container_width=True
        )

    st.markdown("""
    <div class="jnj-footer">
        EPIC AI · SSR Concept Testing POC · J&J Innovative Medicine — Business Technology<br>
        <i>All synthetic outputs are illustrative and generated for demonstration purposes only. Not actual HCP data.</i>
    </div>
    """, unsafe_allow_html=True)

if __name__ == "__main__":
    main()
