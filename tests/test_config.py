"""Tests for the default configuration (``default.yaml`` + pydantic models)."""

from mini_agent.config import get_default_config, render_template

cfg = get_default_config()  # 权威默认值，来自 default.yaml


# --- tool schema ---

def test_bash_tool_has_correct_type():
    assert cfg.tools.bash_tool["type"] == "function"


def test_bash_tool_has_name():
    assert cfg.tools.bash_tool["function"]["name"] == "bash"


def test_bash_tool_has_description():
    assert len(cfg.tools.bash_tool["function"]["description"]) > 0


def test_bash_tool_requires_only_command():
    assert cfg.tools.bash_tool["function"]["parameters"]["required"] == ["command"]


def test_bash_tool_command_is_string():
    props = cfg.tools.bash_tool["function"]["parameters"]["properties"]
    assert props["command"]["type"] == "string"


def test_bash_tool_lines_is_integer():
    props = cfg.tools.bash_tool["function"]["parameters"]["properties"]
    assert "lines" in props
    assert props["lines"]["type"] == "integer"


def test_bash_tool_lines_is_not_required():
    """lines 是可选参数。"""
    required = cfg.tools.bash_tool["function"]["parameters"]["required"]
    assert "lines" not in required


def test_bash_tool_timeout_is_integer():
    """timeout 参数类型应为 integer。"""
    props = cfg.tools.bash_tool["function"]["parameters"]["properties"]
    assert "timeout" in props
    assert props["timeout"]["type"] == "integer"


def test_bash_tool_timeout_is_not_required():
    """timeout 是可选参数。"""
    required = cfg.tools.bash_tool["function"]["parameters"]["required"]
    assert "timeout" not in required


# --- system prompt ---

def test_system_prompt_mentions_bash_tool():
    assert "bash" in cfg.agent.system_prompt.lower()


def test_system_prompt_mentions_submit():
    assert "submit" in cfg.agent.system_prompt.lower()


def test_system_prompt_defines_role():
    """System prompt should give the model a clear expert identity."""
    assert "software engineer" in cfg.agent.system_prompt.lower()


def test_system_prompt_requires_one_command_at_a_time():
    """Single-action discipline: ONE command per turn."""
    assert "one" in cfg.agent.system_prompt.lower() and "command" in cfg.agent.system_prompt.lower()


def test_system_prompt_emphasizes_read_before_edit():
    """Read before you edit — avoids blind changes."""
    assert "read before you edit" in cfg.agent.system_prompt.lower()


def test_system_prompt_emphasizes_smallest_change():
    """Minimal-change principle."""
    assert "smallest change" in cfg.agent.system_prompt.lower()


def test_system_prompt_guides_error_recovery():
    """Don't blindly retry — read errors and adapt."""
    prompt_lower = cfg.agent.system_prompt.lower()
    assert "fail" in prompt_lower or "error" in prompt_lower


def test_system_prompt_requires_verify_before_submit():
    """Verification gate: test or check before calling submit."""
    assert "verify" in cfg.agent.system_prompt.lower()


# --- instance template ---

def test_instance_template_contains_task_placeholder():
    assert "{{ task }}" in cfg.agent.instance_template


def test_instance_template_formats_task():
    result = render_template(cfg.agent.instance_template, task="fix the bug")
    assert "fix the bug" in result


def test_instance_template_contains_workflow():
    """Instance template should include a structured workflow."""
    assert "Explore" in cfg.agent.instance_template
    assert "Diagnose" in cfg.agent.instance_template
    assert "Fix" in cfg.agent.instance_template
    assert "Verify" in cfg.agent.instance_template
    assert "Submit" in cfg.agent.instance_template


def test_instance_template_starts_with_task():
    """Task should appear before the workflow steps."""
    assert cfg.agent.instance_template.startswith("## Task\n")


# --- submit tool ---


def test_submit_tool_has_correct_type():
    assert cfg.tools.submit_tool["type"] == "function"


def test_submit_tool_has_name():
    assert cfg.tools.submit_tool["function"]["name"] == "submit"


def test_submit_tool_has_output_param():
    props = cfg.tools.submit_tool["function"]["parameters"]["properties"]
    assert "output" in props
    assert props["output"]["type"] == "string"


def test_submit_tool_requires_output():
    assert cfg.tools.submit_tool["function"]["parameters"]["required"] == ["output"]


# --- summary prompt (Jinja2 template) ---

def test_summary_prompt_has_placeholders():
    assert "{{ existing_summary }}" in cfg.agent.summary_prompt
    assert "{{ new_lines }}" in cfg.agent.summary_prompt


def test_summary_prompt_renders():
    rendered = render_template(
        cfg.agent.summary_prompt, existing_summary="OLD", new_lines="NEW"
    )
    assert "OLD" in rendered
    assert "NEW" in rendered


# --- defaults ---

def test_default_max_lines_is_positive():
    assert cfg.tools.default_max_lines > 0


def test_default_max_steps_is_positive():
    assert cfg.agent.max_steps > 0


def test_default_timeout_is_positive():
    assert cfg.tools.default_timeout > 0


def test_default_max_time_is_positive():
    assert cfg.agent.max_time > 0


# --- cost tracking ---

def test_default_cost_limit_is_positive():
    assert cfg.agent.cost_limit > 0


def test_input_prices_are_positive():
    assert cfg.cost.price_input_per_1m > 0
    assert cfg.cost.price_input_cache_hit_per_1m > 0


def test_output_price_is_positive():
    assert cfg.cost.price_output_per_1m > 0


def test_cache_hit_is_cheaper_than_miss():
    assert cfg.cost.price_input_cache_hit_per_1m < cfg.cost.price_input_per_1m
