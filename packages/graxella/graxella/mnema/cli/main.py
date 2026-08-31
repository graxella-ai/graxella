from __future__ import annotations

import argparse
import json

from graxella.mnema.integrations.sdk import MnemaClient


def main() -> None:
    """CLI entry point for the Mnema memory runtime."""
    parser = argparse.ArgumentParser(prog="mnema", description="Mnema memory runtime CLI")
    parser.add_argument("--db", default="mnema.db", help="SQLite database path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # snapshot
    p = sub.add_parser("snapshot", help="Current memory state")
    p.add_argument("--agent", required=True)
    p.add_argument("--namespace", default="default")

    # beliefs
    p = sub.add_parser("beliefs", help="List active beliefs")
    p.add_argument("--agent", required=True)
    p.add_argument("--subject", default=None)
    p.add_argument("--namespace", default="default")

    # why
    p = sub.add_parser("why", help="Provenance of an assertion")
    p.add_argument("--id", required=True, dest="assertion_id")

    # timeline
    p = sub.add_parser("timeline", help="Belief history for a subject")
    p.add_argument("--agent", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--namespace", default="default")

    # diff
    p = sub.add_parser("diff", help="What changed between two digest versions")
    p.add_argument("--agent", required=True)
    p.add_argument("--v1", type=int, required=True)
    p.add_argument("--v2", type=int, required=True)
    p.add_argument("--namespace", default="default")

    # report
    p = sub.add_parser("report", help="Full compliance report")
    p.add_argument("--agent", required=True)
    p.add_argument("--namespace", default="default")

    # consolidate
    p = sub.add_parser("consolidate", help="Run sleep-phase consolidation")
    p.add_argument("--agent", required=True)
    p.add_argument("--namespace", default="default")
    p.add_argument("--scenario", default=None, help="FakeLLM scenario (for dev/test)")
    p.add_argument("--model", default="qwen2.5:7b", help="Ollama model (if no --scenario)")

    # replay
    p = sub.add_parser("replay", help="Inspect WAL events in a seq range")
    p.add_argument("--from-seq", type=int, default=0, dest="from_seq")
    p.add_argument("--to-seq", type=int, default=9999, dest="to_seq")

    # digest
    p = sub.add_parser("digest", help="Show a digest (latest or specific version)")
    p.add_argument("--agent", required=True)
    p.add_argument("--namespace", default="default")
    p.add_argument("--version", type=int, default=None)
    p.add_argument("--render", action="store_true", help="Show rendered prompt text")

    args = parser.parse_args()

    def _out(data: object) -> None:
        print(json.dumps(data, indent=2, default=str))

    if args.cmd == "snapshot":
        client = MnemaClient(db_path=args.db, agent_id=args.agent, namespace=args.namespace)
        _out(client.snapshot())

    elif args.cmd == "beliefs":
        from graxella.mnema.adapters.sqlite.repository import SqliteMnemaStore

        store = SqliteMnemaStore(f"sqlite:///{args.db}")
        beliefs = store.current_beliefs(
            namespace=args.namespace, agent_id=args.agent, subject=args.subject
        )
        _out(
            [
                {
                    "id": a.id,
                    "subject": a.subject,
                    "statement": a.statement,
                    "confidence": a.confidence.value,
                    "asserted_at": a.asserted_at.isoformat(),
                }
                for a in beliefs
            ]
        )

    elif args.cmd == "why":
        from graxella.mnema.adapters.sqlite.repository import SqliteMnemaStore
        from graxella.mnema.services.auditor import AuditService

        store = SqliteMnemaStore(f"sqlite:///{args.db}")
        _out(AuditService(store).why_believed(args.assertion_id))

    elif args.cmd == "timeline":
        client = MnemaClient(db_path=args.db, agent_id=args.agent, namespace=args.namespace)
        _out(client.timeline(args.subject))

    elif args.cmd == "diff":
        client = MnemaClient(db_path=args.db, agent_id=args.agent, namespace=args.namespace)
        _out(client.diff(args.v1, args.v2))

    elif args.cmd == "report":
        client = MnemaClient(db_path=args.db, agent_id=args.agent, namespace=args.namespace)
        _out(client.report())

    elif args.cmd == "consolidate":
        llm: object
        if args.scenario:
            from graxella.mnema.adapters.llm.fake import FakeLLM

            llm = FakeLLM.from_scenario(args.scenario)
        else:
            from graxella.mnema.adapters.llm.ollama import OllamaLLM

            llm = OllamaLLM(model=args.model)
        client = MnemaClient(
            db_path=args.db, agent_id=args.agent, namespace=args.namespace, llm=llm
        )
        digest = client.consolidate()
        if digest is None:
            print("Nothing to consolidate (fewer than 2 beliefs).")
        else:
            _out(
                {
                    "version": digest.version,
                    "rules": len(digest.rules),
                    "skills": len(digest.skills),
                    "seq_range": list(digest.source_event_seq_range),
                }
            )

    elif args.cmd == "replay":
        from graxella.mnema.adapters.sqlite.repository import SqliteMnemaStore
        from graxella.mnema.debug.replay import EventReplayer

        store = SqliteMnemaStore(f"sqlite:///{args.db}")
        _out(EventReplayer(store).events_in_range(args.from_seq, args.to_seq))

    elif args.cmd == "digest":
        from graxella.mnema.adapters.sqlite.repository import SqliteMnemaStore

        store = SqliteMnemaStore(f"sqlite:///{args.db}")
        if args.version is not None:
            d = store.get_digest(args.version, namespace=args.namespace, agent_id=args.agent)
        else:
            d = store.latest_digest(namespace=args.namespace, agent_id=args.agent)
        if d is None:
            print("No digest found.")
        elif args.render:
            print(d.render())
        else:
            _out(
                {
                    "version": d.version,
                    "rules": len(d.rules),
                    "skills": len(d.skills),
                    "seq_range": list(d.source_event_seq_range),
                    "created_at": d.created_at.isoformat(),
                }
            )


if __name__ == "__main__":
    main()
