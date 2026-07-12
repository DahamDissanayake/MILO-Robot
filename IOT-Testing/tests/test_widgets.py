from iot_tester.widgets import PassFailPrompt


async def test_pass_fail_prompt_composes_three_widgets() -> None:
    prompt = PassFailPrompt("Did it work?")
    widgets = list(prompt.compose())
    assert len(widgets) == 3


async def test_pass_fail_prompt_resolves_pass() -> None:
    prompt = PassFailPrompt("Did it work?")
    prompt._answer.set_result((True, ""))
    assert await prompt.wait_for_answer() == (True, "")


async def test_pass_fail_prompt_resolves_fail_with_note() -> None:
    prompt = PassFailPrompt("Did it work?")
    prompt._answer.set_result((False, "jitters at 180"))
    assert await prompt.wait_for_answer() == (False, "jitters at 180")
