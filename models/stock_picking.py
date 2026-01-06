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
            "name": _("Change Warehouse"),
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

        Note: When using the Change Warehouse wizard, all chained pickings are
        already included in self, so no need to find/handle destination pickings
        separately here.
        """
        self._check_cancel_back_to_draft_allowed()

        # Cancel pickings that aren't already cancelled
        pickings_to_cancel = self.filtered(lambda p: p.state != "cancel")
        if pickings_to_cancel:
            # Cancel with context flag to preserve chain links
            # This prevents _action_cancel from clearing move_orig_ids/move_dest_ids
            pickings_to_cancel.with_context(preserve_move_chain=True).action_cancel()

        # Set all moves back to draft
        moves = self.mapped("move_ids")
        moves.action_back_to_draft()

    def _check_cancel_back_to_draft_allowed(self):
        """Check if the current user has permission to cancel and set pickings back to draft."""
        if not self.env.user.has_group("stock_picking_cancel_back2draft.group_stock_picking_cancel_back2draft"):
            raise AccessError(_("You do not have permission to cancel and set pickings back to draft."))
