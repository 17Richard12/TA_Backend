import os
from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from tools import search_medical_journal
from prompt import COORDINATOR_PROMPT, SYMPTOM_ANALYZER_PROMPT, HOME_REMEDIES_PROMPT

model = os.getenv("AGENT_MODEL", "gemini-2.5-flash")

# 1. Sub-Agent: Analisis Gejala Paru (Evaluator)
symptom_analyzer = LlmAgent(
    name="symptom_analyzer",
    model=model,
    description="Asisten medis spesialis paru-paru yang menganalisis gejala pernapasan dan efek merokok berdasarkan literatur jurnal medis lokal.",
    instruction=SYMPTOM_ANALYZER_PROMPT,
    tools=[search_medical_journal],
)

# 2. Sub-Agent: Perawatan Rumahan Pernapasan & Konseling Berhenti Merokok (Counselor)
home_remedies = LlmAgent(
    name="home_remedies_advisor",
    model=model,
    description="Penasihat yang memberikan saran perawatan rumahan yang aman, manajemen gejala pernapasan ringan, dan tips berhenti merokok berdasarkan literatur jurnal medis lokal.",
    instruction=HOME_REMEDIES_PROMPT,
    tools=[search_medical_journal],
)

# 3. Root Agent (Monitor / Healthcare Coordinator)
root_agent = LlmAgent(
    name="healthcare_coordinator",
    model=model,
    description="Koordinator utama yang mengelola analisis gejala paru-paru dan saran perawatannya.",
    instruction=COORDINATOR_PROMPT,
    tools=[
        AgentTool(agent=symptom_analyzer),
        AgentTool(agent=home_remedies),
    ],
)
