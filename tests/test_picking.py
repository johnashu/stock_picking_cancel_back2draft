# Modified from Stock back 2 draft by OCA.
# Original code: https://github.com/OCA/stock-logistics-workflow
# © 2025 SJR Nebula
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError, UserError
from odoo.tests.common import TransactionCase


class TestPickingCancelBackToDraft(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src_location = cls.env.ref("stock.stock_location_stock")
        cls.cust_location = cls.env.ref("stock.stock_location_customers")
        cls.partner = cls.env.ref("base.res_partner_2")

        # Find an existing storable product from demo data
        cls.product = cls.env["product.product"].search([("detailed_type", "=", "product")], limit=1)
        if not cls.product:
            # Fallback: create with all required fields
            cls.product = cls.env["product.product"].create(
                {
                    "name": "Test Storable Product",
                    "detailed_type": "product",
                    "sale_line_warn": "no-message",
                    "purchase_line_warn": "no-message",
                }
            )

        # Add the security group to the admin user for non-security tests
        cls.group_cancel_draft = cls.env.ref("stock_picking_cancel_back2draft.group_stock_picking_cancel_back2draft")
        cls.env.user.groups_id = [(4, cls.group_cancel_draft.id)]

        # Create a user with stock user rights but without the cancel_back2draft group
        cls.stock_user = cls.env["res.users"].create(
            {
                "name": "Stock User",
                "login": "stock_user_test",
                "groups_id": [
                    (6, 0, [cls.env.ref("stock.group_stock_user").id]),
                ],
            }
        )
        # Create a user with the cancel_back2draft group
        cls.cancel_draft_user = cls.env["res.users"].create(
            {
                "name": "Cancel Draft User",
                "login": "cancel_draft_user_test",
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("stock.group_stock_user").id,
                            cls.group_cancel_draft.id,
                        ],
                    ),
                ],
            }
        )

    def _create_picking(self):
        picking = self.env["stock.picking"].create(
            {
                "partner_id": self.partner.id,
                "picking_type_id": self.env.ref("stock.picking_type_out").id,
                "location_id": self.src_location.id,
                "location_dest_id": self.cust_location.id,
            }
        )
        self.env["stock.move"].create(
            {
                "name": self.product.name,
                "picking_id": picking.id,
                "product_id": self.product.id,
                "product_uom_qty": 1.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.src_location.id,
                "location_dest_id": self.cust_location.id,
            }
        )
        return picking

    def _add_stock(self, product, quantity, location):
        """Add stock to a location for a product."""
        self.env["stock.quant"]._update_available_quantity(product, location, quantity)

    def test_cancel_back_to_draft_from_draft(self):
        """Test action_cancel_back_to_draft from draft state."""
        picking = self._create_picking()
        self.assertEqual(picking.state, "draft")
        picking.action_cancel_back_to_draft()
        self.assertEqual(picking.state, "draft")

    def test_cancel_back_to_draft_from_confirmed(self):
        """Test action_cancel_back_to_draft from confirmed/assigned state."""
        picking = self._create_picking()
        picking.action_confirm()
        self.assertIn(picking.state, ("confirmed", "assigned", "waiting"))
        picking.action_cancel_back_to_draft()
        self.assertEqual(picking.state, "draft")

    def test_cancel_back_to_draft_from_cancelled(self):
        """Test action_cancel_back_to_draft from cancelled state."""
        picking = self._create_picking()
        picking.action_cancel()
        self.assertEqual(picking.state, "cancel")
        picking.action_cancel_back_to_draft()
        self.assertEqual(picking.state, "draft")

    def test_cancel_back_to_draft_from_done_fails(self):
        """Test that action_cancel_back_to_draft fails on done pickings."""
        picking = self._create_picking()
        self._add_stock(self.product, 10, self.src_location)
        picking.action_confirm()
        picking.action_assign()
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        with self.assertRaises(UserError):
            picking.action_cancel_back_to_draft()

    def test_multiple_pickings(self):
        """Test action_cancel_back_to_draft on multiple pickings at once."""
        picking1 = self._create_picking()
        picking2 = self._create_picking()
        picking1.action_confirm()
        picking2.action_cancel()
        pickings = picking1 | picking2
        pickings.action_cancel_back_to_draft()
        self.assertEqual(picking1.state, "draft")
        self.assertEqual(picking2.state, "draft")

    def test_security_user_without_group_cannot_cancel_back_to_draft(self):
        """Test that user without the group cannot use action_cancel_back_to_draft."""
        picking = self._create_picking()
        picking.action_confirm()
        picking_as_user = picking.with_user(self.stock_user)
        with self.assertRaises(AccessError):
            picking_as_user.action_cancel_back_to_draft()

    def test_security_user_with_group_can_cancel_back_to_draft(self):
        """Test that user with the group can use action_cancel_back_to_draft."""
        picking = self._create_picking()
        picking.action_confirm()
        picking_as_user = picking.with_user(self.cancel_draft_user)
        picking_as_user.action_cancel_back_to_draft()
        self.assertEqual(picking.state, "draft")
