"""Reuse accepted test-host orchestration; substitute only its test fault strategy.

No production module is changed. All production hooks and SDK-generated hook
replies remain the accepted host's genuine paths. This new root never loads old
M12 state. Only the exact outgoing send response is selectively lost.
"""

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import m12_ack_host as fixture
from m12_selective_filter import SelectiveResponseLoss, selective_proof


class ObservedLoss(SelectiveResponseLoss):
    def cut(self):
        root = Path(sys.argv[1])
        with closing(
            sqlite3.connect((root / "p/.proscenium/state.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as db:
            row = db.execute(
                "SELECT body FROM records WHERE namespace='requests' AND id=?",
                (self.client_turn_id,),
            ).fetchone()
            self.facts["application_receipt_before_pipe_close"] = (
                json.loads(row[0])["receipt"] if row else None
            )
        return super().cut()


if __name__ == "__main__":
    fixture.CallbackPipeGate = ObservedLoss
    fixture.accepted_ack_loss = selective_proof
    fixture.main()
