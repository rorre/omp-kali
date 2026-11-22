# Skills

Each skill lives one level under this directory:

```
skills/
  my-skill/
    SKILL.md
    references/
```

omp discovers `skills/*/SKILL.md` non-recursively. `SKILL.md` should start with frontmatter:

```markdown
---
name: my-skill
description: Use when doing a specific task.
---

Instructions, workflows, and commands go here.
```

`name` defaults to the directory name. `description` is required for project-level custom discovery and is what the model uses to decide when the skill applies. Keep referenced assets inside the skill directory and read them with `skill://my-skill/<path>`.

Add or remove skill directories on the host; the container sees them through the `~/.omp/agent/skills` symlink without a restart of omp-kali.
