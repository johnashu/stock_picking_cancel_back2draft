from odoo import _, models
from odoo.exceptions import UserError


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_cancel(self):
        """Override to optionally preserve chain links during cancellation.

        When context has 'preserve_move_chain=True', skip clearing move_orig_ids
        and changing procure_method. This allows cancelling moves while retaining
        the chain for later reuse (e.g., warehouse change in 2-step process).
        """
        if not self.env.context.get("preserve_move_chain"):
            return super()._action_cancel()

        # Same validation as standard
        if any(move.state == "done" and not move.scrapped for move in self):
            raise UserError(
                _(
                    "You cannot cancel a stock move that has been set to 'Done'. "
                    "Create a return in order to reverse the moves which took place."
                )
            )

        moves_to_cancel = self.filtered(lambda m: m.state != "cancel" and not (m.state == "done" and m.scrapped))
        moves_to_cancel.picked = False
        moves_to_cancel._do_unreserve()

        moves_to_cancel.state = "cancel"

        # Handle propagate_cancel but preserve chains
        for move in moves_to_cancel:
            siblings_states = (move.move_dest_ids.mapped("move_orig_ids") - move).mapped("state")
            if move.propagate_cancel:
                if all(state == "cancel" for state in siblings_states):
                    # Cascade cancel but preserve chain (context is inherited)
                    move.move_dest_ids.filtered(lambda m: m.state != "done")._action_cancel()
            # Skip the else branch that breaks chains (procure_method, unlink move_orig_ids)

        # Skip the standard chain-breaking write:
        # moves_to_cancel.write({'move_orig_ids': [(5, 0, 0)], 'procure_method': 'make_to_stock'})

        return True

    def action_back_to_draft(self):
        """Set cancelled moves back to draft state.

        Only moves in 'cancel' state can be set back to draft.
        This is used when changing warehouses but retaining the link
        in a 2-step process.

        Also restores procure_method to 'make_to_order' for moves that have
        upstream chain links (move_orig_ids), ensuring the chain works correctly
        when the picking is re-confirmed.
        """
        if self.filtered(lambda m: m.state != "cancel"):
            raise UserError(_("You can set to draft cancelled moves only"))

        # Restore procure_method for moves with chain links
        # Moves with move_orig_ids should be 'make_to_order' to wait for upstream
        moves_with_chain = self.filtered(lambda m: m.move_orig_ids)
        moves_without_chain = self - moves_with_chain

        if moves_with_chain:
            moves_with_chain.write(
                {
                    "state": "draft",
                    "procure_method": "make_to_order",
                }
            )
        if moves_without_chain:
            moves_without_chain.write({"state": "draft"})
