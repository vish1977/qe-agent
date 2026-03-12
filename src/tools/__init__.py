from .github import GITHUB_TOOLS, execute_github_tool
from .jira import JIRA_TOOLS, execute_jira_tool
from .testing import TESTING_TOOLS, execute_testing_tool
from .code import CODE_TOOLS, execute_code_tool

ALL_TOOLS = GITHUB_TOOLS + JIRA_TOOLS + TESTING_TOOLS + CODE_TOOLS


def execute_tool(name: str, input_data: dict) -> str:
    """Route a tool call to the correct executor."""
    github_names = {t["name"] for t in GITHUB_TOOLS}
    jira_names = {t["name"] for t in JIRA_TOOLS}
    testing_names = {t["name"] for t in TESTING_TOOLS}
    code_names = {t["name"] for t in CODE_TOOLS}

    if name in github_names:
        return execute_github_tool(name, input_data)
    elif name in jira_names:
        return execute_jira_tool(name, input_data)
    elif name in testing_names:
        return execute_testing_tool(name, input_data)
    elif name in code_names:
        return execute_code_tool(name, input_data)
    else:
        return f"Error: unknown tool '{name}'"
