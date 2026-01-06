# © 2025 SJR Nebula
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestChangeWarehouse(TransactionCase):
    """Test cases for changing warehouse on 2-step pickings."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cust_location = cls.env.ref("stock.stock_location_customers")
        cls.supplier_location = cls.env.ref("stock.stock_location_suppliers")
        cls.partner = cls.env.ref("base.res_partner_2")

        # Always create our own test products (no tracking) for predictable tests
        cls.product = cls.env["product.product"].create(
            {
                "name": "Test Storable Product",
                "detailed_type": "product",
                "tracking": "none",
                "sale_line_warn": "no-message",
            }
        )

        # Create a serial-tracked product for serial number tests
        cls.serial_product = cls.env["product.product"].create(
            {
                "name": "Test Serial Product",
                "detailed_type": "product",
                "tracking": "serial",
                "sale_line_warn": "no-message",
            }
        )

        # Add the security group to the admin user
        cls.group_cancel_draft = cls.env.ref("stock_picking_cancel_back2draft.group_stock_picking_cancel_back2draft")
        cls.env.user.groups_id = [(4, cls.group_cancel_draft.id)]

        # Get or create two warehouses with 2-step delivery (same company)
        cls.warehouse1 = cls.env["stock.warehouse"].search([], limit=1)
        cls.warehouse1.delivery_steps = "pick_ship"

        # Create second warehouse in the SAME company
        cls.warehouse2 = cls.env["stock.warehouse"].search(
            [("id", "!=", cls.warehouse1.id), ("company_id", "=", cls.warehouse1.company_id.id)], limit=1
        )
        if not cls.warehouse2:
            cls.warehouse2 = cls.env["stock.warehouse"].create(
                {
                    "name": "Test Warehouse 2",
                    "code": "WH2",
                    "company_id": cls.warehouse1.company_id.id,
                }
            )
        cls.warehouse2.delivery_steps = "pick_ship"

    def _create_two_step_delivery(self, warehouse):
        """Create a 2-step delivery chain using procurement (like a Sale Order)."""
        procurement_group = self.env["procurement.group"].create(
            {
                "name": "Test Sale Order",
            }
        )

        delivery_route = warehouse.delivery_route_id

        ProcurementGroup = self.env["procurement.group"]
        procurement = ProcurementGroup.Procurement(
            self.product,
            5.0,
            self.product.uom_id,
            self.cust_location,
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

        return pick_picking, ship_picking

    def test_change_warehouse_wizard_creation(self):
        """Test that the change warehouse wizard opens correctly on confirmed picking."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Verify picking is confirmed (wizard should work on any non-done state)
        self.assertIn(pick.state, ("confirmed", "assigned", "waiting"))

        # Open wizard - should work on confirmed pickings now
        action = pick.action_open_change_warehouse_wizard()
        self.assertEqual(action["res_model"], "stock.picking.change.warehouse")

    def test_change_warehouse_full_flow(self):
        """Test changing warehouse performs full flow: cancel -> draft -> change -> confirm."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Verify pickings start in confirmed state
        self.assertIn(pick.state, ("confirmed", "assigned", "waiting"))

        # Create wizard on confirmed picking (no need to cancel first anymore)
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": True,
                }
            )
        )

        # Verify wizard computed chained pickings
        self.assertIn(pick, wizard.chained_picking_ids)
        self.assertIn(ship, wizard.chained_picking_ids)

        # Execute warehouse change - this will cancel, reset to draft, change, and confirm
        wizard.action_change_warehouse()

        # Verify pick was updated
        self.assertEqual(pick.picking_type_id, self.warehouse2.pick_type_id)
        self.assertEqual(pick.location_id, self.warehouse2.lot_stock_id)
        self.assertEqual(pick.location_dest_id, self.warehouse2.wh_output_stock_loc_id)

        # Verify ship was updated
        self.assertEqual(ship.picking_type_id, self.warehouse2.out_type_id)
        self.assertEqual(ship.location_id, self.warehouse2.wh_output_stock_loc_id)
        self.assertEqual(ship.location_dest_id, self.cust_location)

        # Verify moves were updated
        pick_move = pick.move_ids
        ship_move = ship.move_ids

        self.assertEqual(pick_move.warehouse_id, self.warehouse2)
        self.assertEqual(ship_move.warehouse_id, self.warehouse2)

        # CRITICAL: Verify chain is still preserved
        self.assertEqual(pick_move.move_dest_ids, ship_move)
        self.assertEqual(ship_move.move_orig_ids, pick_move)

        # Verify pickings are confirmed (marked as "To Do")
        self.assertIn(pick.state, ("confirmed", "assigned", "waiting"))
        self.assertIn(ship.state, ("confirmed", "waiting"))

    def test_change_warehouse_preserves_so_link(self):
        """Test that changing warehouse preserves the sale_line_id link."""
        # Skip if sale_stock not installed
        if "sale_line_id" not in self.env["stock.move"]._fields:
            self.skipTest("sale_stock module not installed")

        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Simulate SO link (normally set by sale_stock)
        fake_sale_line_id = 12345
        pick.move_ids.write({"sale_line_id": fake_sale_line_id})
        ship.move_ids.write({"sale_line_id": fake_sale_line_id})

        # Change warehouse (will auto-cancel and reset)
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": True,
                }
            )
        )
        wizard.action_change_warehouse()

        # Verify sale_line_id preserved
        self.assertEqual(pick.move_ids.sale_line_id.id, fake_sale_line_id)
        self.assertEqual(ship.move_ids.sale_line_id.id, fake_sale_line_id)

    def test_change_warehouse_preserves_procurement_group(self):
        """Test that changing warehouse preserves the procurement group."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        original_group = pick.group_id

        # Change warehouse (will auto-cancel and reset)
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": True,
                }
            )
        )
        wizard.action_change_warehouse()

        # Verify group_id preserved
        self.assertEqual(pick.group_id, original_group)
        self.assertEqual(ship.group_id, original_group)

    def test_change_warehouse_on_confirmed_picking(self):
        """Test that changing warehouse works on confirmed picking (auto-cancels first)."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Verify picking is confirmed
        self.assertIn(pick.state, ("confirmed", "assigned", "waiting"))

        # Open wizard on confirmed picking - should work now
        action = pick.action_open_change_warehouse_wizard()
        self.assertEqual(action["res_model"], "stock.picking.change.warehouse")

        # Create wizard and execute
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": True,
                }
            )
        )
        wizard.action_change_warehouse()

        # Verify pickings were updated and confirmed
        self.assertEqual(pick.picking_type_id, self.warehouse2.pick_type_id)
        self.assertIn(pick.state, ("confirmed", "assigned", "waiting"))

    def test_change_warehouse_without_chained(self):
        """Test changing warehouse without including chained pickings."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Create wizard without chained pickings (on confirmed picking)
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": False,
                }
            )
        )

        # Verify only pick is included (not ship)
        self.assertEqual(wizard.picking_count, 1)

    def test_change_warehouse_fails_on_done_picking(self):
        """Test that changing warehouse fails on done pickings."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Add stock and complete the pick
        self.env["stock.quant"]._update_available_quantity(self.product, self.warehouse1.lot_stock_id, 10)
        pick.action_assign()
        for move in pick.move_ids:
            move.quantity = move.product_uom_qty
        pick.button_validate()

        self.assertEqual(pick.state, "done")

        # Try to open wizard on done picking - should fail
        with self.assertRaises(UserError):
            pick.action_open_change_warehouse_wizard()

    def _create_two_step_receipt(self, warehouse):
        """Create a 2-step receipt chain (WH/IN -> Internal Transfer)."""
        # Ensure warehouse has 2-step receipt
        original_steps = warehouse.reception_steps
        warehouse.reception_steps = "two_steps"

        input_location = warehouse.wh_input_stock_loc_id

        # Create the receipt picking (Supplier -> Input)
        receipt_picking = self.env["stock.picking"].create(
            {
                "picking_type_id": warehouse.in_type_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": input_location.id,
            }
        )
        self.env["stock.move"].create(
            {
                "name": self.product.name,
                "picking_id": receipt_picking.id,
                "product_id": self.product.id,
                "product_uom_qty": 5.0,
                "product_uom": self.product.uom_id.id,
                "location_id": self.supplier_location.id,
                "location_dest_id": input_location.id,
            }
        )

        # Confirm to trigger push rule
        receipt_picking.action_confirm()

        # Find the internal transfer
        internal_picking = self.env["stock.picking"].search(
            [
                ("picking_type_id", "=", warehouse.int_type_id.id),
                ("location_id", "=", input_location.id),
                ("state", "!=", "cancel"),
            ],
            order="id desc",
            limit=1,
        )

        return receipt_picking, internal_picking, original_steps

    def test_change_warehouse_two_step_receipt(self):
        """Test changing warehouse on 2-step receipt (WH/IN -> Internal Transfer)."""
        # Setup warehouse2 for 2-step receipt too
        self.warehouse2.reception_steps = "two_steps"

        receipt, internal, original_steps = self._create_two_step_receipt(self.warehouse1)

        try:
            self.assertTrue(internal, "Internal transfer should be created by push rule")

            # Verify chain
            receipt_move = receipt.move_ids
            internal_move = internal.move_ids
            self.assertIn(internal_move, receipt_move.move_dest_ids)

            # Change warehouse on receipt
            wizard = (
                self.env["stock.picking.change.warehouse"]
                .with_context(
                    active_ids=[receipt.id],
                    active_model="stock.picking",
                )
                .create(
                    {
                        "new_warehouse_id": self.warehouse2.id,
                        "include_chained_pickings": True,
                    }
                )
            )

            # Verify both pickings are found
            self.assertIn(receipt, wizard.chained_picking_ids)
            self.assertIn(internal, wizard.chained_picking_ids)

            # Execute change
            wizard.action_change_warehouse()

            # Verify receipt updated
            self.assertEqual(receipt.picking_type_id, self.warehouse2.in_type_id)

            # Verify internal updated
            self.assertEqual(internal.picking_type_id, self.warehouse2.int_type_id)
            self.assertEqual(internal.location_id, self.warehouse2.wh_input_stock_loc_id)
            self.assertEqual(internal.location_dest_id, self.warehouse2.lot_stock_id)

            # CRITICAL: Verify chain preserved
            self.assertIn(internal_move, receipt_move.move_dest_ids)
            self.assertIn(receipt_move, internal_move.move_orig_ids)

        finally:
            self.warehouse1.reception_steps = original_steps

    def test_change_warehouse_with_assigned_serial_numbers(self):
        """Test changing warehouse when serial numbers are already assigned.

        This tests the fix for the 'picked' flag issue where move lines with
        assigned serials have picked=True and weren't being properly unreserved.
        """
        # Setup warehouse2 for 2-step receipt
        self.warehouse2.reception_steps = "two_steps"
        original_steps = self.warehouse1.reception_steps
        self.warehouse1.reception_steps = "two_steps"

        try:
            input_location = self.warehouse1.wh_input_stock_loc_id

            # Create receipt with serial product
            receipt = self.env["stock.picking"].create(
                {
                    "picking_type_id": self.warehouse1.in_type_id.id,
                    "location_id": self.supplier_location.id,
                    "location_dest_id": input_location.id,
                }
            )
            receipt_move = self.env["stock.move"].create(
                {
                    "name": self.serial_product.name,
                    "picking_id": receipt.id,
                    "product_id": self.serial_product.id,
                    "product_uom_qty": 3.0,
                    "product_uom": self.serial_product.uom_id.id,
                    "location_id": self.supplier_location.id,
                    "location_dest_id": input_location.id,
                }
            )

            receipt.action_confirm()

            # Find internal transfer
            internal = self.env["stock.picking"].search(
                [
                    ("picking_type_id", "=", self.warehouse1.int_type_id.id),
                    ("location_id", "=", input_location.id),
                    ("state", "!=", "cancel"),
                ],
                order="id desc",
                limit=1,
            )

            # Clear any auto-created move lines before adding our serials
            receipt_move.move_line_ids.unlink()

            # Assign serial numbers to receipt (simulating user input)
            for i in range(3):
                lot = self.env["stock.lot"].create(
                    {
                        "name": f"SN-TEST-{i+1}",
                        "product_id": self.serial_product.id,
                        "company_id": self.warehouse1.company_id.id,
                    }
                )
                self.env["stock.move.line"].create(
                    {
                        "move_id": receipt_move.id,
                        "picking_id": receipt.id,
                        "product_id": self.serial_product.id,
                        "product_uom_id": self.serial_product.uom_id.id,
                        "location_id": self.supplier_location.id,
                        "location_dest_id": input_location.id,
                        "lot_id": lot.id,
                        "quantity": 1.0,
                    }
                )

            # Verify move lines exist and receipt is assigned
            self.assertEqual(len(receipt_move.move_line_ids), 3)
            self.assertEqual(receipt.state, "assigned")

            # Change warehouse - this should work even with assigned serials
            wizard = (
                self.env["stock.picking.change.warehouse"]
                .with_context(
                    active_ids=[receipt.id],
                    active_model="stock.picking",
                )
                .create(
                    {
                        "new_warehouse_id": self.warehouse2.id,
                        "include_chained_pickings": True,
                    }
                )
            )

            # This should NOT raise an error
            wizard.action_change_warehouse()

            # Verify pickings updated
            self.assertEqual(receipt.picking_type_id, self.warehouse2.in_type_id)
            self.assertIn(receipt.state, ("confirmed", "assigned", "waiting"))

            # Verify chain preserved
            if internal:
                internal_move = internal.move_ids
                self.assertIn(internal_move, receipt_move.move_dest_ids)

        finally:
            self.warehouse1.reception_steps = original_steps

    def test_chain_works_after_warehouse_change_and_validation(self):
        """Test that the chain still works correctly after warehouse change.

        After changing warehouse and re-confirming:
        1. Pick should be confirmable and assignable
        2. When Pick is validated, Ship should become ready
        3. Serial numbers should flow from Pick to Ship
        """
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Add stock to warehouse1
        self.env["stock.quant"]._update_available_quantity(self.product, self.warehouse1.lot_stock_id, 10)

        # Change to warehouse2
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": True,
                }
            )
        )
        wizard.action_change_warehouse()

        # Add stock to warehouse2
        self.env["stock.quant"]._update_available_quantity(self.product, self.warehouse2.lot_stock_id, 10)

        # Assign pick
        pick.action_assign()
        self.assertEqual(pick.state, "assigned")

        # Validate pick
        for move in pick.move_ids:
            move.quantity = move.product_uom_qty
        pick.button_validate()

        self.assertEqual(pick.state, "done")

        # Ship should now be ready (assigned) because chain is preserved
        ship.action_assign()
        self.assertEqual(ship.state, "assigned")

        # Verify the chain link is still there
        pick_move = pick.move_ids
        ship_move = ship.move_ids
        self.assertEqual(pick_move.move_dest_ids, ship_move)
        self.assertEqual(ship_move.move_orig_ids, pick_move)

    def test_serial_flow_after_warehouse_change(self):
        """Test that serial numbers flow correctly after warehouse change.

        This is the critical test for 2-step delivery with serial products:
        1. Create Pick -> Ship chain with serial product
        2. Change warehouse
        3. Add stock with serials to new warehouse
        4. Validate Pick with serials
        5. Verify serials appear on Ship
        """
        # Setup warehouses for 2-step
        self.warehouse1.delivery_steps = "pick_ship"
        self.warehouse2.delivery_steps = "pick_ship"

        # Create procurement with serial product
        procurement_group = self.env["procurement.group"].create(
            {
                "name": "Test Serial Sale",
            }
        )

        ProcurementGroup = self.env["procurement.group"]
        procurement = ProcurementGroup.Procurement(
            self.serial_product,
            2.0,
            self.serial_product.uom_id,
            self.cust_location,
            "Test Serial Delivery",
            "TEST/SERIAL/001",
            self.warehouse1.company_id,
            {
                "group_id": procurement_group,
                "warehouse_id": self.warehouse1,
                "route_ids": self.warehouse1.delivery_route_id,
            },
        )
        ProcurementGroup.run([procurement])

        # Find pickings
        ship = self.env["stock.picking"].search(
            [
                ("group_id", "=", procurement_group.id),
                ("picking_type_id", "=", self.warehouse1.out_type_id.id),
            ]
        )
        pick = self.env["stock.picking"].search(
            [
                ("group_id", "=", procurement_group.id),
                ("picking_type_id", "=", self.warehouse1.pick_type_id.id),
            ]
        )

        self.assertEqual(len(pick), 1)
        self.assertEqual(len(ship), 1)

        # Change warehouse
        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse2.id,
                    "include_chained_pickings": True,
                }
            )
        )
        wizard.action_change_warehouse()

        # Create serial numbers and add stock to warehouse2
        serials = []
        for i in range(2):
            lot = self.env["stock.lot"].create(
                {
                    "name": f"SERIAL-WH2-{i+1}",
                    "product_id": self.serial_product.id,
                    "company_id": self.warehouse2.company_id.id,
                }
            )
            serials.append(lot)
            self.env["stock.quant"]._update_available_quantity(
                self.serial_product,
                self.warehouse2.lot_stock_id,
                1.0,
                lot_id=lot,
            )

        # Assign pick
        pick.action_assign()
        self.assertEqual(pick.state, "assigned")

        # Verify serials are on pick move lines
        pick_move = pick.move_ids
        self.assertEqual(len(pick_move.move_line_ids), 2)
        pick_serials = pick_move.move_line_ids.mapped("lot_id")
        self.assertEqual(set(pick_serials.ids), set(s.id for s in serials))

        # Validate pick
        pick.button_validate()
        self.assertEqual(pick.state, "done")

        # Ship should now be assignable
        ship.action_assign()
        self.assertEqual(ship.state, "assigned")

        # CRITICAL: Verify serials flowed to ship
        ship_move = ship.move_ids
        ship_serials = ship_move.move_line_ids.mapped("lot_id")
        self.assertEqual(
            set(ship_serials.ids),
            set(s.id for s in serials),
            "Serial numbers should flow from Pick to Ship after warehouse change",
        )

    def test_change_warehouse_same_warehouse_fails(self):
        """Test that changing to the same warehouse raises an error."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        wizard = (
            self.env["stock.picking.change.warehouse"]
            .with_context(
                active_ids=[pick.id],
                active_model="stock.picking",
            )
            .create(
                {
                    "new_warehouse_id": self.warehouse1.id,
                    "include_chained_pickings": True,
                }
            )
        )

        with self.assertRaises(UserError):
            wizard.action_change_warehouse()

    # test_change_warehouse_no_warehouse_selected_fails removed:
    # new_warehouse_id is required=True at DB level, so wizard creation
    # without it fails before action_change_warehouse() can be tested.
    # The same_warehouse_fails test covers the validation logic.

    def test_multiple_deliveries_with_serials_after_warehouse_change(self):
        """Test 10 two-step deliveries with serial products after warehouse change.

        1. Creates 10 pick->ship chains in warehouse1
        2. Changes ALL to warehouse2
        3. Adds stock with serials to warehouse2
        4. Validates ALL picks first, THEN validates ALL ships
        5. Verifies serial numbers flow correctly through the entire chain
        """
        num_deliveries = 10
        serials_per_delivery = 3

        # Create multiple serial-tracked products
        serial_products = []
        for i in range(3):
            product = self.env["product.product"].create(
                {
                    "name": f"Serial Product {i+1}",
                    "detailed_type": "product",
                    "tracking": "serial",
                    "sale_line_warn": "no-message",
                }
            )
            serial_products.append(product)

        # Store all picks, ships, and their expected serials
        deliveries = []

        # Create 10 two-step deliveries in warehouse1
        for i in range(num_deliveries):
            product = serial_products[i % len(serial_products)]

            # Create the 2-step delivery chain in warehouse1
            pick, ship = self._create_two_step_delivery_with_product(self.warehouse1, product, serials_per_delivery)

            deliveries.append(
                {
                    "pick": pick,
                    "ship": ship,
                    "product": product,
                    "serials": [],  # Will be populated after warehouse change
                    "index": i + 1,
                }
            )

        # Change ALL deliveries to warehouse2 BEFORE adding serials
        for delivery in deliveries:
            pick = delivery["pick"]
            wizard = (
                self.env["stock.picking.change.warehouse"]
                .with_context(
                    active_ids=[pick.id],
                    active_model="stock.picking",
                )
                .create(
                    {
                        "new_warehouse_id": self.warehouse2.id,
                        "include_chained_pickings": True,
                    }
                )
            )
            wizard.action_change_warehouse()

            # Verify warehouse changed - picking types
            self.assertEqual(
                pick.picking_type_id, self.warehouse2.pick_type_id, f"Pick {delivery['index']} should be in warehouse2"
            )
            self.assertEqual(
                delivery["ship"].picking_type_id,
                self.warehouse2.out_type_id,
                f"Ship {delivery['index']} should be in warehouse2",
            )
            # Verify locations changed to warehouse2
            self.assertEqual(
                pick.location_id,
                self.warehouse2.lot_stock_id,
                f"Pick {delivery['index']} source should be warehouse2 stock",
            )
            self.assertEqual(
                pick.location_dest_id,
                self.warehouse2.wh_output_stock_loc_id,
                f"Pick {delivery['index']} dest should be warehouse2 output",
            )
            self.assertEqual(
                delivery["ship"].location_id,
                self.warehouse2.wh_output_stock_loc_id,
                f"Ship {delivery['index']} source should be warehouse2 output",
            )

        # Now create serials and add stock to warehouse2
        for delivery in deliveries:
            product = delivery["product"]
            serials = []

            for j in range(serials_per_delivery):
                lot = self.env["stock.lot"].create(
                    {
                        "name": f"BATCH-{delivery['index']}-SN-{j+1}",
                        "product_id": product.id,
                        "company_id": self.warehouse2.company_id.id,
                    }
                )
                serials.append(lot)

                # Add stock with this serial to warehouse2
                self.env["stock.quant"]._update_available_quantity(product, self.warehouse2.lot_stock_id, 1, lot_id=lot)

            delivery["serials"] = serials

        # Assign and validate ALL picks first
        for delivery in deliveries:
            pick = delivery["pick"]
            pick.action_assign()
            self.assertEqual(pick.state, "assigned", f"Pick {delivery['index']} should be assigned")

            # Set quantities on move lines
            for move in pick.move_ids:
                for ml in move.move_line_ids:
                    ml.quantity = ml.quantity_product_uom

            pick.button_validate()
            self.assertEqual(pick.state, "done", f"Pick {delivery['index']} should be done")

        # Verify all picks are done before processing ships
        for delivery in deliveries:
            self.assertEqual(delivery["pick"].state, "done")

        # Now assign and validate ALL ships
        for delivery in deliveries:
            ship = delivery["ship"]
            ship.action_assign()
            self.assertEqual(ship.state, "assigned", f"Ship {delivery['index']} should be assigned after pick done")

            # Verify serials flowed from pick to ship
            pick_serials = set(delivery["pick"].move_ids.move_line_ids.mapped("lot_id.name"))
            ship_serials = set(ship.move_ids.move_line_ids.mapped("lot_id.name"))
            expected_serials = set(lot.name for lot in delivery["serials"])

            self.assertEqual(pick_serials, expected_serials, f"Pick {delivery['index']} should have expected serials")
            self.assertEqual(
                ship_serials, expected_serials, f"Ship {delivery['index']} should have same serials as pick"
            )

            # Validate ship
            for move in ship.move_ids:
                for ml in move.move_line_ids:
                    ml.quantity = ml.quantity_product_uom

            ship.button_validate()
            self.assertEqual(ship.state, "done", f"Ship {delivery['index']} should be done")

        # Final verification - all deliveries complete with correct serials
        for delivery in deliveries:
            pick = delivery["pick"]
            ship = delivery["ship"]

            self.assertEqual(pick.state, "done")
            self.assertEqual(ship.state, "done")

            # Verify chain links preserved
            pick_move = pick.move_ids
            ship_move = ship.move_ids
            self.assertEqual(pick_move.move_dest_ids, ship_move)
            self.assertEqual(ship_move.move_orig_ids, pick_move)

            # Verify final serial numbers match expected
            final_serials = set(ship.move_ids.move_line_ids.mapped("lot_id.name"))
            expected_serials = set(lot.name for lot in delivery["serials"])
            self.assertEqual(
                final_serials, expected_serials, f"Delivery {delivery['index']} final serials should match"
            )

    def _create_two_step_delivery_with_product(self, warehouse, product, qty):
        """Create a 2-step delivery chain for a specific product and quantity."""
        procurement_group = self.env["procurement.group"].create({"name": f"Test Delivery {product.name}"})

        delivery_route = warehouse.delivery_route_id

        self.env["procurement.group"].run(
            [
                self.env["procurement.group"].Procurement(
                    product,
                    qty,
                    product.uom_id,
                    self.cust_location,
                    product.name,
                    f"TEST/{product.name}",
                    warehouse.company_id,
                    {
                        "warehouse_id": warehouse,
                        "group_id": procurement_group,
                        "route_ids": delivery_route,
                    },
                )
            ]
        )

        # Find the created pickings
        pickings = self.env["stock.picking"].search([("group_id", "=", procurement_group.id)], order="id")

        pick = pickings.filtered(lambda p: p.picking_type_id == warehouse.pick_type_id)
        ship = pickings.filtered(lambda p: p.picking_type_id == warehouse.out_type_id)

        self.assertTrue(pick, "Pick should be created")
        self.assertTrue(ship, "Ship should be created")

        return pick, ship

    def test_multiple_receipts_with_serials_after_warehouse_change(self):
        """Test 10 two-step receipts with serial products after warehouse change.

        1. Creates 10 receipt->internal chains in warehouse1 (unique product per receipt)
        2. Changes ALL to warehouse2
        3. Assigns serials to receipts
        4. Validates ALL receipts first, THEN validates ALL internals
        5. Verifies serial numbers flow correctly through the entire chain
        """
        # Set warehouses to 2-step reception
        original_steps1 = self.warehouse1.reception_steps
        original_steps2 = self.warehouse2.reception_steps
        self.warehouse1.reception_steps = "two_steps"
        self.warehouse2.reception_steps = "two_steps"

        try:
            num_receipts = 10
            serials_per_receipt = 3

            # Store all receipts data
            receipts_data = []

            # Create 10 two-step receipts in warehouse1 - UNIQUE product per receipt
            for i in range(num_receipts):
                # Create unique product for each receipt to avoid move merging
                product = self.env["product.product"].create(
                    {
                        "name": f"Receipt Serial Product {i+1}",
                        "detailed_type": "product",
                        "tracking": "serial",
                        "sale_line_warn": "no-message",
                    }
                )

                # Create the receipt
                input_location = self.warehouse1.wh_input_stock_loc_id
                receipt = self.env["stock.picking"].create(
                    {
                        "picking_type_id": self.warehouse1.in_type_id.id,
                        "location_id": self.supplier_location.id,
                        "location_dest_id": input_location.id,
                        "partner_id": self.partner.id,
                    }
                )
                self.env["stock.move"].create(
                    {
                        "name": product.name,
                        "picking_id": receipt.id,
                        "product_id": product.id,
                        "product_uom_qty": serials_per_receipt,
                        "product_uom": product.uom_id.id,
                        "location_id": self.supplier_location.id,
                        "location_dest_id": input_location.id,
                    }
                )
                receipt.action_confirm()

                # Find the internal transfer via move chain
                receipt_move = receipt.move_ids
                internal_move = receipt_move.move_dest_ids
                internal = internal_move.picking_id if internal_move else None

                self.assertTrue(internal, f"Internal should be created for receipt {i+1}")

                receipts_data.append(
                    {
                        "receipt": receipt,
                        "internal": internal,
                        "product": product,
                        "serials": [],
                        "index": i + 1,
                    }
                )

            # Change ALL receipts to warehouse2 BEFORE adding serials
            for data in receipts_data:
                receipt = data["receipt"]
                wizard = (
                    self.env["stock.picking.change.warehouse"]
                    .with_context(
                        active_ids=[receipt.id],
                        active_model="stock.picking",
                    )
                    .create(
                        {
                            "new_warehouse_id": self.warehouse2.id,
                            "include_chained_pickings": True,
                        }
                    )
                )
                wizard.action_change_warehouse()

                # Verify warehouse changed for both receipt and internal
                self.assertEqual(
                    receipt.picking_type_id,
                    self.warehouse2.in_type_id,
                    f"Receipt {data['index']} should be in warehouse2",
                )
                # Verify receipt locations changed to warehouse2
                self.assertEqual(
                    receipt.location_dest_id,
                    self.warehouse2.wh_input_stock_loc_id,
                    f"Receipt {data['index']} dest should be warehouse2 input",
                )

                # Re-fetch internal via move chain (it should have been updated)
                receipt_move = receipt.move_ids
                internal_move = receipt_move.move_dest_ids
                data["internal"] = internal_move.picking_id if internal_move else None

                if data["internal"]:
                    self.assertEqual(
                        data["internal"].picking_type_id,
                        self.warehouse2.int_type_id,
                        f"Internal {data['index']} should be in warehouse2",
                    )
                    # Verify internal locations changed to warehouse2
                    self.assertEqual(
                        data["internal"].location_id,
                        self.warehouse2.wh_input_stock_loc_id,
                        f"Internal {data['index']} source should be warehouse2 input",
                    )
                    self.assertEqual(
                        data["internal"].location_dest_id,
                        self.warehouse2.lot_stock_id,
                        f"Internal {data['index']} dest should be warehouse2 stock",
                    )

            # Now create serials and assign to receipts
            for data in receipts_data:
                product = data["product"]
                receipt = data["receipt"]
                receipt_move = receipt.move_ids
                serials = []

                # Clear any auto-created move lines
                receipt_move.move_line_ids.unlink()

                for j in range(serials_per_receipt):
                    lot = self.env["stock.lot"].create(
                        {
                            "name": f"RCV-{data['index']}-SN-{j+1}",
                            "product_id": product.id,
                            "company_id": self.warehouse2.company_id.id,
                        }
                    )
                    serials.append(lot)

                    # Create move line with serial
                    self.env["stock.move.line"].create(
                        {
                            "move_id": receipt_move.id,
                            "picking_id": receipt.id,
                            "product_id": product.id,
                            "product_uom_id": product.uom_id.id,
                            "location_id": receipt_move.location_id.id,
                            "location_dest_id": receipt_move.location_dest_id.id,
                            "lot_id": lot.id,
                            "quantity": 1.0,
                        }
                    )

                data["serials"] = serials

            # Validate ALL receipts first
            for data in receipts_data:
                receipt = data["receipt"]
                self.assertIn(receipt.state, ("assigned", "confirmed"), f"Receipt {data['index']} should be ready")
                receipt.button_validate()
                self.assertEqual(receipt.state, "done", f"Receipt {data['index']} should be done")

            # Now assign and validate ALL internal transfers
            # Note: Odoo may merge internal moves into shared pickings, so we track via move chain
            processed_internals = set()
            for data in receipts_data:
                internal = data["internal"]
                if not internal or internal.id in processed_internals:
                    continue

                internal.action_assign()
                self.assertIn(
                    internal.state,
                    ("assigned", "partially_available"),
                    f"Internal {data['index']} should be assigned after receipt done",
                )
                processed_internals.add(internal.id)

            # Verify serials via move chain (not via picking which may be shared)
            for data in receipts_data:
                receipt_move = data["receipt"].move_ids
                internal_move = receipt_move.move_dest_ids

                receipt_serials = set(receipt_move.move_line_ids.mapped("lot_id.name"))
                internal_serials = set(internal_move.move_line_ids.mapped("lot_id.name"))
                expected_serials = set(lot.name for lot in data["serials"])

                self.assertEqual(
                    receipt_serials, expected_serials, f"Receipt {data['index']} should have expected serials"
                )
                self.assertEqual(
                    internal_serials, expected_serials, f"Internal {data['index']} should have same serials as receipt"
                )

            # Validate all internal pickings
            validated_internals = set()
            for data in receipts_data:
                internal = data["internal"]
                if not internal or internal.id in validated_internals:
                    continue

                for move in internal.move_ids:
                    for ml in move.move_line_ids:
                        ml.quantity = ml.quantity_product_uom

                internal.button_validate()
                self.assertEqual(internal.state, "done", "Internal should be done")
                validated_internals.add(internal.id)

            # Final verification - chain links and serials via move relationships
            for data in receipts_data:
                receipt = data["receipt"]
                receipt_move = receipt.move_ids
                internal_move = receipt_move.move_dest_ids

                self.assertEqual(receipt.state, "done")
                self.assertEqual(internal_move.state, "done")

                # Verify chain links preserved
                self.assertEqual(internal_move.move_orig_ids, receipt_move)

                # Verify final serial numbers match (via the specific move, not the whole picking)
                final_serials = set(internal_move.move_line_ids.mapped("lot_id.name"))
                expected_serials = set(lot.name for lot in data["serials"])
                self.assertEqual(final_serials, expected_serials, f"Receipt {data['index']} final serials should match")

        finally:
            self.warehouse1.reception_steps = original_steps1
            self.warehouse2.reception_steps = original_steps2
