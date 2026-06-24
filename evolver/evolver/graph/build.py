"""LangGraph wiring: inner loop + human-gated outer loop.

    ingest → analyst → paper_trade → evaluate ─┬─ done (END)         [inner loop]
                                               └─ optimize → critic ─┬─ deploy → END
                                                                     └─ done (END)

`interrupt_before=["deploy"]` pauses the graph; the Telegram /approve handler
resumes it via graph.invoke(None, config={"configurable": {"thread_id": tid}}).
"""
from __future__ import annotations

from langgraph.graph import StateGraph, START, END

from evolver.graph.state import LoopState
from evolver.graph.nodes import (
    ingest, analyst_node, paper_trade_node, evaluate_node,
    critic_node, deploy_node, route_after_eval, route_after_critic,
)


def build_graph(checkpointer=None):
    g = StateGraph(LoopState)
    g.add_node("ingest", ingest)
    g.add_node("analyst", analyst_node)
    g.add_node("paper_trade", paper_trade_node)
    g.add_node("evaluate", evaluate_node)
    g.add_node("critic", critic_node)
    g.add_node("deploy", deploy_node)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "analyst")
    g.add_edge("analyst", "paper_trade")
    g.add_edge("paper_trade", "evaluate")
    g.add_conditional_edges("evaluate", route_after_eval, {"optimize": "critic", "done": END})
    g.add_conditional_edges("critic", route_after_critic, {"deploy": "deploy", "done": END})
    g.add_edge("deploy", END)

    # Human-in-the-loop gate before any promotion.
    return g.compile(checkpointer=checkpointer, interrupt_before=["deploy"])


if __name__ == "__main__":  # `python -m evolver.graph.build` prints the mermaid
    print(build_graph().get_graph().draw_mermaid())
