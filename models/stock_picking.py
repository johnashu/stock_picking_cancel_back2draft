from odoo import _, models
from odoo.exceptions import AccessError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def action_cancel_back_to_draft(self):
        """Cancel pickings first (if not already cancelled), then set to draft.
        This works on pickings in any state apart from 'done'.
        in 'done' state, action_cancel will throw an error.
        """
        self._check_cancel_back_to_draft_allowed()

        pickings_to_cancel = self.filtered(lambda p: p.state != "cancel")
        if pickings_to_cancel:
            pickings_to_cancel.action_cancel()

        moves = self.mapped("move_ids")
        moves.action_back_to_draft()

    def _check_cancel_back_to_draft_allowed(self):
        """Check if the current user has permission to cancel and set pickings back to draft."""
        if not self.env.user.has_group("stock_picking_cancel_back2draft.group_stock_picking_cancel_back2draft"):
            raise AccessError(_("You do not have permission to cancel and set pickings back to draft."))
