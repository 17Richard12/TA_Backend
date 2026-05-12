import os
from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools import google_search  # ✅ FIX
from prompt import COORDINATOR_PROMPT, SYMPTOM_ANALYZER_PROMPT, HOME_REMEDIES_PROMPT

model = "gemini-2.5-flash"

# 1. Sub-Agent: Analisis Gejala
symptom_analyzer = LlmAgent(
    name="symptom_analyzer",
    model=model,
    description="Friendly healthcare assistant with multimodal capabilities to analyze symptoms.",
    instruction=SYMPTOM_ANALYZER_PROMPT,
    tools=[google_search]
)

# 2. Sub-Agent: Rawatan Rumahan
home_remedies = LlmAgent(
    name="home_remedies_advisor",
    model=model,
    description="Advisor that suggests natural , safe remedies for light symptoms.",
    instruction=HOME_REMEDIES_PROMPT,
    tools=[google_search]
)

# 3. Root Agent
root_agent = LlmAgent(
    name="healthcare_coordinator",
    model=model,
    description="Main healthcare coordinator managing symptom analyzer and home remedies.",  # ✅ FIX
    instruction=COORDINATOR_PROMPT,
    tools=[
        AgentTool(agent=symptom_analyzer),
        AgentTool(agent=home_remedies)
    ]
)