from odoo import _, models
from odoo.exceptions import AccessError, UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def action_open_change_warehouse_wizard(self):
        """Open the Change Warehouse wizard.

        This will automatically cancel and set pickings back to draft if needed,
        then open the wizard to change the warehouse.
        """
        self._check_cancel_back_to_draft_allowed()

        # Check for done pickings - these cannot be changed
        done_pickings = self.filtered(lambda p: p.state == "done")
        if done_pickings:
            raise UserError(
                _("Cannot change warehouse for completed pickings: %s") % ", ".join(done_pickings.mapped("name"))
            )

        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking.change.warehouse",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_ids": self.ids,
                "active_model": "stock.picking",
            },
        }

    def action_cancel_back_to_draft(self):
        """Cancel pickings first (if not already cancelled), then set to draft.

        Preserves the Pick <-> Out chain links so that when the Pick is
        re-validated, serials flow correctly to the Out operation.

        This works on pickings in any state apart from 'done'.
        In 'done' state, action_cancel will throw an error.
        """
        self._check_cancel_back_to_draft_allowed()

        pickings_to_cancel = self.filtered(lambda p: p.state != "cancel")
        cancelled_dest_move_ids = []

        if pickings_to_cancel:
            moves = pickings_to_cancel.mapped("move_ids")

            # Track destination move IDs that will potentially be cancelled (propagate_cancel=True)
            # Store IDs to avoid cache issues when checking state after cancel
            dest_moves_to_track = moves.filtered(lambda m: m.propagate_cancel).mapped("move_dest_ids")
            dest_move_ids_to_track = dest_moves_to_track.filtered(lambda m: m.state not in ("done", "cancel")).ids

            # For cancel_back_to_draft, also find linked destination pickings that should be reset
            # This handles chains where propagate_cancel=False but we still want to reset the chain
            # We do this at the picking level to respect chain relationships
            dest_pickings_to_reset = self.env["stock.picking"]
            for move in moves:
                dest_moves = move.move_dest_ids.filtered(
                    lambda m: m.state in ("waiting", "confirmed", "assigned") and m.picking_id
                )
                dest_pickings_to_reset |= dest_moves.mapped("picking_id")

            # Cancel with context flag to preserve chain links
            # This prevents _action_cancel from clearing move_orig_ids/move_dest_ids
            pickings_to_cancel.with_context(preserve_move_chain=True).action_cancel()

            # Re-fetch destination moves to get fresh state from database
            if dest_move_ids_to_track:
                dest_moves_refreshed = self.env["stock.move"].browse(dest_move_ids_to_track)
                # Invalidate cache to ensure we get fresh state
                dest_moves_refreshed.invalidate_recordset(["state"])
                cancelled_dest_move_ids = dest_moves_refreshed.filtered(lambda m: m.state == "cancel").ids

            # For linked destination pickings that weren't cancelled via propagate_cancel,
            # explicitly cancel them (with chain preservation) so they can be reset to draft
            if dest_pickings_to_reset:
                # Filter to only pickings that are still in resettable states (not already cancelled)
                dest_pickings_to_reset = dest_pickings_to_reset.filtered(
                    lambda p: p.state in ("waiting", "confirmed", "assigned")
                )
                if dest_pickings_to_reset:
                    # Cancel these pickings with chain preservation
                    dest_pickings_to_reset.with_context(preserve_move_chain=True).action_cancel()
                    # Track their moves to reset to draft (avoid duplicates)
                    dest_move_ids = dest_pickings_to_reset.mapped("move_ids").ids
                    cancelled_dest_move_ids = list(set(cancelled_dest_move_ids + dest_move_ids))

        moves = self.mapped("move_ids")
        moves.action_back_to_draft()

        # Also set destination moves back to draft if they were cancelled as part of this operation
        if cancelled_dest_move_ids:
            cancelled_dest_moves = self.env["stock.move"].browse(cancelled_dest_move_ids)
            cancelled_dest_moves.action_back_to_draft()

    def _check_cancel_back_to_draft_allowed(self):
        """Check if the current user has permission to cancel and set pickings back to draft."""
        if not self.env.user.has_group("stock_picking_cancel_back2draft.group_stock_picking_cancel_back2draft"):
            raise AccessError(_("You do not have permission to cancel and set pickings back to draft."))
