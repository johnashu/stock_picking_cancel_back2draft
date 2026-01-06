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

    def test_two_step_delivery_via_procurement(self):
        """Test 2-step delivery (Pick + Ship) created via procurement like a Sale Order.

        This mimics how a Sale Order creates a 2-step delivery chain:
        1. Set warehouse to 'pick_ship' (2-step delivery)
        2. Run procurement to customer location using delivery route
        3. Odoo creates: Pick (Stock -> Output) -> Ship (Output -> Customer)
        4. Cancel the Ship, verify chain is preserved
        """
        warehouse = self.env["stock.warehouse"].search([], limit=1)

        # Save original setting to restore later
        original_delivery_steps = warehouse.delivery_steps

        try:
            # Configure warehouse for 2-step delivery (Pick + Ship)
            warehouse.delivery_steps = "pick_ship"

            # Create procurement group (like a sale order would)
            procurement_group = self.env["procurement.group"].create(
                {
                    "name": "Test Sale Order",
                }
            )

            # Get the delivery route
            delivery_route = warehouse.delivery_route_id

            # Run procurement - this is exactly what sale order does
            ProcurementGroup = self.env["procurement.group"]
            procurement = ProcurementGroup.Procurement(
                self.product,
                5.0,
                self.product.uom_id,
                self.cust_location,  # Destination: Customer
                "Test Delivery",
                "TEST/SALE/001",
                warehouse.company_id,
                {
                    "group_id": procurement_group,
                    "warehouse_id": warehouse,
                    "route_ids": delivery_route,
                },
            )
            ProcurementGroup.run([procurement])

            # Find the created pickings
            ship_picking = self.env["stock.picking"].search(
                [
                    ("group_id", "=", procurement_group.id),
                    ("picking_type_id", "=", warehouse.out_type_id.id),
                ]
            )
            pick_picking = self.env["stock.picking"].search(
                [
                    ("group_id", "=", procurement_group.id),
                    ("picking_type_id", "=", warehouse.pick_type_id.id),
                ]
            )

            self.assertEqual(len(ship_picking), 1, "Should have 1 Ship picking")
            self.assertEqual(len(pick_picking), 1, "Should have 1 Pick picking")

            ship_move = ship_picking.move_ids
            pick_move = pick_picking.move_ids

            # Verify chain was created by procurement
            self.assertEqual(pick_move.move_dest_ids, ship_move)
            self.assertEqual(ship_move.move_orig_ids, pick_move)
            self.assertEqual(ship_move.procure_method, "make_to_order")

            # Verify states
            self.assertIn(ship_picking.state, ("confirmed", "waiting"))
            self.assertIn(pick_picking.state, ("confirmed", "assigned", "waiting"))

            # Cancel the Pick and set to draft - should cascade to Ship
            pick_picking.action_cancel_back_to_draft()

            # Verify both are now in draft
            self.assertEqual(pick_picking.state, "draft")
            self.assertEqual(ship_picking.state, "draft")

            # CRITICAL: Verify chain links are preserved
            self.assertEqual(
                pick_move.move_dest_ids, ship_move, "Chain link pick_move.move_dest_ids should be preserved"
            )
            self.assertEqual(
                ship_move.move_orig_ids, pick_move, "Chain link ship_move.move_orig_ids should be preserved"
            )
            self.assertEqual(ship_move.procure_method, "make_to_order", "procure_method should be preserved")

        finally:
            # Restore original warehouse setting
            warehouse.delivery_steps = original_delivery_steps

    def test_two_step_receipt_via_procurement(self):
        """Test 2-step receipt (Input + Stock) created via procurement like a Purchase Order.

        This mimics how a Purchase Order creates a 2-step receipt chain:
        1. Set warehouse to 'two_steps' (2-step receipt)
        2. Run procurement/create receipt from supplier
        3. Odoo creates: Receipt (Supplier -> Input) -> Internal (Input -> Stock)
        4. Cancel the Receipt, verify chain is preserved
        """
        warehouse = self.env["stock.warehouse"].search([], limit=1)
        supplier_location = self.env.ref("stock.stock_location_suppliers")

        # Save original setting to restore later
        original_reception_steps = warehouse.reception_steps

        try:
            # Configure warehouse for 2-step receipt (Input + Stock)
            warehouse.reception_steps = "two_steps"

            # For receipts, we create the IN picking directly (like a PO would)
            # The push rule will create the internal transfer

            # First, let's find or create the input location
            input_location = warehouse.wh_input_stock_loc_id

            # Create the receipt picking (Supplier -> Input)
            receipt_picking = self.env["stock.picking"].create(
                {
                    "picking_type_id": warehouse.in_type_id.id,
                    "location_id": supplier_location.id,
                    "location_dest_id": input_location.id,
                }
            )
            receipt_move = self.env["stock.move"].create(
                {
                    "name": self.product.name,
                    "picking_id": receipt_picking.id,
                    "product_id": self.product.id,
                    "product_uom_qty": 10.0,
                    "product_uom": self.product.uom_id.id,
                    "location_id": supplier_location.id,
                    "location_dest_id": input_location.id,
                }
            )

            # Confirm the receipt - this triggers push rules to create internal transfer
            receipt_picking.action_confirm()

            # Find the internal transfer that was created by push rule
            internal_picking = self.env["stock.picking"].search(
                [
                    ("picking_type_id", "=", warehouse.int_type_id.id),
                    ("location_id", "=", input_location.id),
                    ("location_dest_id", "=", warehouse.lot_stock_id.id),
                    ("state", "!=", "cancel"),
                ],
                order="id desc",
                limit=1,
            )

            # If push rule created internal transfer, test the chain
            if internal_picking:
                internal_move = internal_picking.move_ids.filtered(lambda m: m.product_id == self.product)

                if internal_move:
                    # Verify chain was created
                    self.assertIn(internal_move, receipt_move.move_dest_ids)
                    self.assertIn(receipt_move, internal_move.move_orig_ids)

                    # Cancel the receipt and set to draft
                    receipt_picking.action_cancel_back_to_draft()

                    # Verify states
                    self.assertEqual(receipt_picking.state, "draft")

                    # Verify chain is preserved
                    self.assertIn(
                        internal_move, receipt_move.move_dest_ids, "Chain link should be preserved after cancel"
                    )

        finally:
            # Restore original warehouse setting
            warehouse.reception_steps = original_reception_steps

    def test_manual_two_step_chain_preserved(self):
        """Test manually created 2-step chain (fallback test without procurement).

        This test doesn't rely on warehouse configuration, useful for simpler setups.
        """
        warehouse = self.env["stock.warehouse"].search([], limit=1)
        stock_location = warehouse.lot_stock_id

        # Create or find output location
        output_location = warehouse.wh_output_stock_loc_id
        if not output_location or not output_location.active:
            output_location = self.env["stock.location"].create(
                {
                    "name": "Test Output",
                    "usage": "internal",
                    "location_id": warehouse.view_location_id.id,
                }
            )

        # Create Pick: Stock -> Output
        pick = self.env["stock.picking"].create(
            {
                "picking_type_id": warehouse.pick_type_id.id or warehouse.int_type_id.id,
                "location_id": stock_location.id,
                "location_dest_id": output_location.id,
            }
        )
        pick_move = self.env["stock.move"].create(
            {
                "name": self.product.name,
                "picking_id": pick.id,
                "product_id": self.product.id,
                "product_uom_qty": 5.0,
                "product_uom": self.product.uom_id.id,
                "location_id": stock_location.id,
                "location_dest_id": output_location.id,
                "propagate_cancel": True,
            }
        )

        # Create Ship: Output -> Customer
        ship = self.env["stock.picking"].create(
            {
                "picking_type_id": self.env.ref("stock.picking_type_out").id,
                "location_id": output_location.id,
                "location_dest_id": self.cust_location.id,
            }
        )
        ship_move = self.env["stock.move"].create(
            {
                "name": self.product.name,
                "picking_id": ship.id,
                "product_id": self.product.id,
                "product_uom_qty": 5.0,
                "product_uom": self.product.uom_id.id,
                "location_id": output_location.id,
                "location_dest_id": self.cust_location.id,
                "procure_method": "make_to_order",
            }
        )

        # Link the moves: Pick -> Ship
        pick_move.move_dest_ids = [(4, ship_move.id)]

        # Confirm both pickings
        pick.action_confirm()
        ship.action_confirm()

        # Verify chain is set up
        self.assertEqual(pick_move.move_dest_ids, ship_move)
        self.assertEqual(ship_move.move_orig_ids, pick_move)

        # Cancel and set to draft
        pick.action_cancel_back_to_draft()

        # Verify both are draft
        self.assertEqual(pick.state, "draft")
        self.assertEqual(ship.state, "draft")

        # CRITICAL: Verify chain links preserved
        self.assertEqual(pick_move.move_dest_ids, ship_move)
        self.assertEqual(ship_move.move_orig_ids, pick_move)
        self.assertEqual(ship_move.procure_method, "make_to_order")
