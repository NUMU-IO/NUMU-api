import re

with open("src/api/v1/routes/stores/themes.py", encoding="utf-8") as f:
    code = f.read()

# Replace _build_statuses[build_id] = { ... } assignments
code = re.sub(
    r"_build_statuses\[build_id\]\s*=\s*({[^}]*})",
    r"await get_theme_build_store().set(build_id, \1)",
    code,
    flags=re.DOTALL,
)

# Replace _build_statuses[build_id]["status"] = ...
code = code.replace(
    '_build_statuses[build_id]["status"] = ThemeBuildStatus.FAILED\n        _build_statuses[build_id]["error"] = "Failed to queue build task"',
    'await get_theme_build_store().update(build_id, {"status": ThemeBuildStatus.FAILED, "error": "Failed to queue build task"})',
)

code = code.replace(
    '_build_statuses[build_id]["status"] = ThemeBuildStatus.FAILED\n        _build_statuses[build_id]["message"] = "Failed to start build task"',
    'await get_theme_build_store().update(build_id, {"status": ThemeBuildStatus.FAILED, "message": "Failed to start build task"})',
)

# Replace build_info = _build_statuses.get(build_id)
code = code.replace(
    "build_info = _build_statuses.get(build_id)",
    "build_info = await get_theme_build_store().get(build_id)",
)

with open("src/api/v1/routes/stores/themes.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Replacement complete.")
