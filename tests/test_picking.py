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

    def test_cancel_back_to_draft_with_picked_move_lines(self):
        """Test cancel_back_to_draft works when move lines have picked=True.

        This tests the fix where move lines with picked=True (e.g., receipts with
        assigned serial numbers) weren't being properly unreserved during cancel.
        """
        # Create a serial-tracked product
        # Create via template to avoid field issues with product.product
        serial_template = self.env["product.template"].create(
            {
                "name": "Test Serial Product",
                "detailed_type": "product",
                "tracking": "serial",
                "sale_line_warn": "no-message",
            }
        )
        serial_product = serial_template.product_variant_id

        supplier_location = self.env.ref("stock.stock_location_suppliers")
        warehouse = self.env["stock.warehouse"].search([], limit=1)

        # Create a receipt
        receipt = self.env["stock.picking"].create(
            {
                "picking_type_id": warehouse.in_type_id.id,
                "location_id": supplier_location.id,
                "location_dest_id": warehouse.lot_stock_id.id,
            }
        )
        receipt_move = self.env["stock.move"].create(
            {
                "name": serial_product.name,
                "picking_id": receipt.id,
                "product_id": serial_product.id,
                "product_uom_qty": 3.0,
                "product_uom": serial_product.uom_id.id,
                "location_id": supplier_location.id,
                "location_dest_id": warehouse.lot_stock_id.id,
            }
        )

        receipt.action_confirm()

        # Clear any auto-created move lines before adding our serials
        receipt_move.move_line_ids.unlink()

        # Create serial numbers and assign them (simulating user scanning serials)
        for i in range(3):
            lot = self.env["stock.lot"].create(
                {
                    "name": f"SN-PICKED-{i+1}",
                    "product_id": serial_product.id,
                    "company_id": warehouse.company_id.id,
                }
            )
            self.env["stock.move.line"].create(
                {
                    "move_id": receipt_move.id,
                    "picking_id": receipt.id,
                    "product_id": serial_product.id,
                    "product_uom_id": serial_product.uom_id.id,
                    "location_id": supplier_location.id,
                    "location_dest_id": warehouse.lot_stock_id.id,
                    "lot_id": lot.id,
                    "quantity": 1.0,
                }
            )

        # Verify move lines exist and receipt is assigned
        self.assertEqual(len(receipt_move.move_line_ids), 3)
        self.assertEqual(receipt.state, "assigned")

        # This is the critical test - should NOT raise an error
        receipt.action_cancel_back_to_draft()

        # Verify receipt is now draft
        self.assertEqual(receipt.state, "draft")

        # Verify move is draft
        self.assertEqual(receipt_move.state, "draft")

    def test_cancel_back_to_draft_assigned_picking_with_stock(self):
        """Test cancel_back_to_draft on assigned picking with reserved stock."""
        picking = self._create_picking()
        self._add_stock(self.product, 10, self.src_location)
        picking.action_confirm()
        picking.action_assign()

        self.assertEqual(picking.state, "assigned")
        self.assertTrue(picking.move_ids.move_line_ids, "Should have reserved move lines")

        # Cancel and set to draft
        picking.action_cancel_back_to_draft()

        self.assertEqual(picking.state, "draft")
        self.assertFalse(picking.move_ids.move_line_ids, "Move lines should be removed after cancel")
