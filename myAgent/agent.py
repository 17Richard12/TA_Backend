import os
from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools import google_search
from prompt import COORDINATOR_PROMPT, SYMPTOM_ANALYZER_PROMPT, HOME_REMEDIES_PROMPT

model = "gemini-3.5-flash"

# 1. Sub-Agent: Analisis Gejala Paru
symptom_analyzer = LlmAgent(
    name="symptom_analyzer",
    model=model,
    description="Asisten medis spesialis paru-paru yang menganalisis gejala pernapasan dan efek merokok.", # <-- Diperbarui
    instruction=SYMPTOM_ANALYZER_PROMPT,
    tools=[google_search]
)

# 2. Sub-Agent: Rawatan Rumahan Pernapasan
home_remedies = LlmAgent(
    name="home_remedies_advisor",
    model=model,
    description="Penasihat yang memberikan saran perawatan alami dan aman untuk melegakan saluran pernapasan.", # <-- Diperbarui
    instruction=HOME_REMEDIES_PROMPT,
    tools=[google_search]
)

# 3. Root Agent
root_agent = LlmAgent(
    name="healthcare_coordinator",
    model=model,
    description="Koordinator utama yang mengelola analisis gejala paru-paru dan saran perawatannya.", # <-- Diperbarui
    instruction=COORDINATOR_PROMPT,
    tools=[
        AgentTool(agent=symptom_analyzer),
        AgentTool(agent=home_remedies)
    ]
)