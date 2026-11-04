# factorio-runner

Go-Explore episodes of LLM agents playing Factorio 2.0 on a lockstep clock: a node is a paused
save + the agent's work dir (design source, NOTES.md); an episode restores a node, gets a goal,
plays through tools, and offers the nodes it reaches to its arm's archive.

```sh
python -m unittest discover -s tests -t .           # FACTORIO_BIN=... adds the real-server test
python -m runner fresh   arms/test-scripted.toml --store ./store
python -m runner episode arms/test-scripted.toml --store ./store
python -m runner show    arms/test-scripted.toml --store ./store
```

Arms are TOML files in `arms/`. Store: a directory or `s3://bucket/prefix`. In CI the `run`
workflow dispatches `fresh`, `episode`, `show` or `ping` for an arm.
