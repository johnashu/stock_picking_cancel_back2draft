# © 2025 SJR Nebula
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests.common import Form, TransactionCase


class TestChangeWarehouse(TransactionCase):
    """Test cases for changing warehouse on 2-step pickings.

    These tests focus on batch operations with multiple SOs/POs, multiple serials,
    and multiple warehouses to ensure the warehouse change functionality works
    correctly at scale.
    """

    NUM_ORDERS = 10
    SERIALS_PER_ORDER = 10

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cust_location = cls.env.ref("stock.stock_location_customers")
        cls.supplier_location = cls.env.ref("stock.stock_location_suppliers")
        cls.partner = cls.env.ref("base.res_partner_2")

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

    def test_change_warehouse_fails_on_done_picking(self):
        """Test that changing warehouse fails on done pickings."""
        # Create a simple product for this test
        product = self.env["product.product"].create(
            {
                "name": "Test Product Done Check",
                "detailed_type": "product",
                "tracking": "none",
                "sale_line_warn": "no-message",
            }
        )
        pick, ship = self._create_two_step_delivery_with_product(self.warehouse1, product, 5)

        # Add stock and complete the pick
        self.env["stock.quant"]._update_available_quantity(product, self.warehouse1.lot_stock_id, 10)
        pick.action_assign()
        for move in pick.move_ids:
            move.quantity = move.product_uom_qty
        pick.button_validate()

        self.assertEqual(pick.state, "done")

        # Try to open wizard on done picking - should fail
        with self.assertRaises(UserError):
            pick.action_open_change_warehouse_wizard()

    def test_change_warehouse_same_warehouse_fails(self):
        """Test that changing to the same warehouse raises an error."""
        product = self.env["product.product"].create(
            {
                "name": "Test Product Same WH Check",
                "detailed_type": "product",
                "tracking": "none",
                "sale_line_warn": "no-message",
            }
        )
        pick, ship = self._create_two_step_delivery_with_product(self.warehouse1, product, 5)

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

    def test_multiple_deliveries_with_serials_after_warehouse_change(self):
        """Test multiple two-step deliveries with serial products after warehouse change.

        Flow:
        1. Create 10 SOs (pick->ship chains) in warehouse1 with serial products
        2. Change ALL to warehouse2
        3. Add stock with 10 serials per order to warehouse2
        4. Validate ALL picks
        5. Check OUT pickings retained moves and serials
        6. Validate ALL ships
        """
        # Store all picks, ships, and their expected serials
        deliveries = []

        # Create 10 two-step deliveries in warehouse1 with unique serial products
        for i in range(self.NUM_ORDERS):
            # Create unique serial product per order to avoid move merging
            product = self.env["product.product"].create(
                {
                    "name": f"Delivery Serial Product {i+1}",
                    "detailed_type": "product",
                    "tracking": "serial",
                    "sale_line_warn": "no-message",
                }
            )

            # Create the 2-step delivery chain in warehouse1
            pick, ship = self._create_two_step_delivery_with_product(self.warehouse1, product, self.SERIALS_PER_ORDER)

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

            for j in range(self.SERIALS_PER_ORDER):
                lot = self.env["stock.lot"].create(
                    {
                        "name": f"SO-{delivery['index']}-SN-{j+1}",
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

        # Check OUT pickings retained moves and serials BEFORE validating ships
        for delivery in deliveries:
            ship = delivery["ship"]
            ship.action_assign()
            self.assertEqual(ship.state, "assigned", f"Ship {delivery['index']} should be assigned after pick done")

            # Verify chain links preserved
            pick_move = delivery["pick"].move_ids
            ship_move = ship.move_ids
            self.assertEqual(pick_move.move_dest_ids, ship_move, f"Ship {delivery['index']} should be linked to pick")
            self.assertEqual(ship_move.move_orig_ids, pick_move, f"Pick {delivery['index']} should be origin of ship")

            # Verify serials flowed from pick to ship
            pick_serials = set(delivery["pick"].move_ids.move_line_ids.mapped("lot_id.name"))
            ship_serials = set(ship.move_ids.move_line_ids.mapped("lot_id.name"))
            expected_serials = set(lot.name for lot in delivery["serials"])

            self.assertEqual(pick_serials, expected_serials, f"Pick {delivery['index']} should have expected serials")
            self.assertEqual(
                ship_serials, expected_serials, f"Ship {delivery['index']} should have same serials as pick"
            )

        # Now validate ALL ships
        for delivery in deliveries:
            ship = delivery["ship"]

            # Set quantities on move lines
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

    def test_multiple_receipts_with_serials_after_warehouse_change(self):
        """Test multiple two-step receipts with serial products after warehouse change.

        Flow:
        1. Create 10 POs (receipt->internal chains) in warehouse1 with serial products
        2. Change ALL to warehouse2
        3. Assign 10 serials per order to receipts
        4. Validate ALL receipts (IN pickings)
        5. Check INT pickings retained moves and serials
        6. Validate ALL internal transfers
        """
        # Set warehouses to 2-step reception
        original_steps1 = self.warehouse1.reception_steps
        original_steps2 = self.warehouse2.reception_steps
        self.warehouse1.reception_steps = "two_steps"
        self.warehouse2.reception_steps = "two_steps"

        try:
            # Store all receipts data
            receipts_data = []

            # Create 10 two-step receipts in warehouse1 - UNIQUE product per receipt
            for i in range(self.NUM_ORDERS):
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
                        "product_uom_qty": self.SERIALS_PER_ORDER,
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

                # Verify warehouse changed for receipt
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

                for j in range(self.SERIALS_PER_ORDER):
                    lot = self.env["stock.lot"].create(
                        {
                            "name": f"PO-{data['index']}-SN-{j+1}",
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

            # Validate ALL receipts (IN pickings) first
            for data in receipts_data:
                receipt = data["receipt"]
                self.assertIn(receipt.state, ("assigned", "confirmed"), f"Receipt {data['index']} should be ready")
                receipt.button_validate()
                self.assertEqual(receipt.state, "done", f"Receipt {data['index']} should be done")

            # Check INT pickings retained moves and serials BEFORE validating internals
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

                # Verify chain links preserved
                self.assertEqual(
                    internal_move.move_orig_ids,
                    receipt_move,
                    f"Internal {data['index']} should be linked to receipt",
                )

                receipt_serials = set(receipt_move.move_line_ids.mapped("lot_id.name"))
                internal_serials = set(internal_move.move_line_ids.mapped("lot_id.name"))
                expected_serials = set(lot.name for lot in data["serials"])

                self.assertEqual(
                    receipt_serials, expected_serials, f"Receipt {data['index']} should have expected serials"
                )
                self.assertEqual(
                    internal_serials, expected_serials, f"Internal {data['index']} should have same serials as receipt"
                )

            # Validate all internal pickings (INT)
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

    def test_multiple_deliveries_partial_pick_with_backorders_warehouse_change(self):
        """Test multiple deliveries with partial picks creating backorders, then changing warehouse.

        Flow:
        1. Create 10 SOs (pick->ship chains) in warehouse1 with serial products
        2. Add stock to warehouse1 for HALF the quantity (5 serials per order)
        3. Validate ALL picks PARTIALLY (creates backorders)
        4. Validate ALL ships (for the partial quantities)
        5. Change warehouse for ALL backorders to warehouse2
        6. Add stock to warehouse2 for remaining quantity (5 serials per order)
        7. Validate ALL backorder picks
        8. Validate ALL backorder ships
        """
        # Store all picks, ships, backorders, and their expected serials
        deliveries = []
        backorder_picks = []
        backorder_ships = []

        # Create 10 two-step deliveries in warehouse1 with unique serial products
        for i in range(self.NUM_ORDERS):
            # Create unique serial product per order to avoid move merging
            product = self.env["product.product"].create(
                {
                    "name": f"Partial Pick Serial Product {i+1}",
                    "detailed_type": "product",
                    "tracking": "serial",
                    "sale_line_warn": "no-message",
                }
            )

            # Create the 2-step delivery chain in warehouse1
            pick, ship = self._create_two_step_delivery_with_product(self.warehouse1, product, self.SERIALS_PER_ORDER)

            deliveries.append(
                {
                    "pick": pick,
                    "ship": ship,
                    "product": product,
                    "initial_serials": [],  # First 5 serials in WH1
                    "backorder_serials": [],  # Last 5 serials in WH2
                    "index": i + 1,
                }
            )

        # Add stock to warehouse1 for HALF the quantity (5 serials per order)
        for delivery in deliveries:
            product = delivery["product"]
            initial_serials = []

            for j in range(self.SERIALS_PER_ORDER // 2):  # First 5 serials
                lot = self.env["stock.lot"].create(
                    {
                        "name": f"SO-{delivery['index']}-SN-{j+1}",
                        "product_id": product.id,
                        "company_id": self.warehouse1.company_id.id,
                    }
                )
                initial_serials.append(lot)

                # Add stock with this serial to warehouse1
                self.env["stock.quant"]._update_available_quantity(product, self.warehouse1.lot_stock_id, 1, lot_id=lot)

            delivery["initial_serials"] = initial_serials

        # Assign and validate ALL picks PARTIALLY (creating backorders)
        for delivery in deliveries:
            pick = delivery["pick"]
            pick.action_assign()
            # With only half the stock available, it should be partially_available or assigned
            self.assertIn(
                pick.state,
                ("assigned", "partially_available"),
                f"Pick {delivery['index']} should be assigned/partially available",
            )

            # Set quantities on move lines - Odoo should have created move lines only for available serials
            move = pick.move_ids
            # Verify we have move lines for the available serials
            available_move_lines = move.move_line_ids.filtered(lambda ml: ml.lot_id in delivery["initial_serials"])
            self.assertEqual(
                len(available_move_lines),
                len(delivery["initial_serials"]),
                f"Pick {delivery['index']} should have move lines for available serials",
            )

            # Set quantities for all move lines (they should already be 1.0, but ensure it)
            for ml in move.move_line_ids:
                ml.quantity = ml.quantity_product_uom

            # Validate with backorder (remaining quantities will create backorder)
            # button_validate() may return a wizard action for partial validation
            action = pick.button_validate()
            if action:
                # Handle the wizard (immediate transfer or backorder wizard)
                wizard_model = action.get("res_model")
                if wizard_model:
                    wizard = Form(self.env[wizard_model].with_context(action["context"])).save()
                    wizard.process()
            self.assertEqual(pick.state, "done", f"Pick {delivery['index']} should be done")

            # Find backorder pick
            backorder_pick = self.env["stock.picking"].search(
                [
                    ("backorder_id", "=", pick.id),
                    ("picking_type_id", "=", self.warehouse1.pick_type_id.id),
                ],
                limit=1,
            )
            if backorder_pick:
                backorder_picks.append(
                    {
                        "pick": backorder_pick,
                        "original_delivery": delivery,
                    }
                )

        # Verify all initial picks are done
        for delivery in deliveries:
            self.assertEqual(delivery["pick"].state, "done", f"Initial pick {delivery['index']} should be done")

        # Validate ALL ships for the partial quantities
        for delivery in deliveries:
            ship = delivery["ship"]
            ship.action_assign()
            self.assertEqual(ship.state, "assigned", f"Ship {delivery['index']} should be assigned after pick done")

            # Verify chain links preserved
            # After partial validation, the pick move is split - check the done move
            pick_move_done = delivery["pick"].move_ids.filtered(lambda m: m.state == "done")
            ship_move = ship.move_ids
            # The done pick move should be linked to the ship move
            self.assertIn(
                ship_move, pick_move_done.move_dest_ids, f"Ship {delivery['index']} should be linked to done pick move"
            )
            self.assertIn(
                pick_move_done, ship_move.move_orig_ids, f"Done pick move {delivery['index']} should be origin of ship"
            )

            # Set quantities on move lines
            for move in ship.move_ids:
                for ml in move.move_line_ids:
                    ml.quantity = ml.quantity_product_uom

            # Validate ship (may need wizard for partial validation)
            action = ship.button_validate()
            if action:
                wizard_model = action.get("res_model")
                if wizard_model:
                    wizard = Form(self.env[wizard_model].with_context(action["context"])).save()
                    wizard.process()
            self.assertEqual(ship.state, "done", f"Ship {delivery['index']} should be done")

            # Find backorder ship
            backorder_ship = self.env["stock.picking"].search(
                [
                    ("backorder_id", "=", ship.id),
                    ("picking_type_id", "=", self.warehouse1.out_type_id.id),
                ],
                limit=1,
            )
            if backorder_ship:
                backorder_ships.append(
                    {
                        "ship": backorder_ship,
                        "original_delivery": delivery,
                    }
                )

        # Verify we have backorders
        self.assertTrue(backorder_picks, "Should have backorder picks")
        self.assertTrue(backorder_ships, "Should have backorder ships")

        # Change warehouse for ALL backorder picks to warehouse2
        for backorder_data in backorder_picks:
            backorder_pick = backorder_data["pick"]
            wizard = (
                self.env["stock.picking.change.warehouse"]
                .with_context(
                    active_ids=[backorder_pick.id],
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

            # Verify warehouse changed for backorder pick
            self.assertEqual(
                backorder_pick.picking_type_id,
                self.warehouse2.pick_type_id,
                "Backorder pick should be in warehouse2",
            )
            self.assertEqual(
                backorder_pick.location_id,
                self.warehouse2.lot_stock_id,
                "Backorder pick source should be warehouse2 stock",
            )
            self.assertEqual(
                backorder_pick.location_dest_id,
                self.warehouse2.wh_output_stock_loc_id,
                "Backorder pick dest should be warehouse2 output",
            )

            # Find the corresponding backorder ship and verify it was updated
            delivery = backorder_data["original_delivery"]
            backorder_ship = next((bs["ship"] for bs in backorder_ships if bs["original_delivery"] == delivery), None)
            if backorder_ship:
                self.assertEqual(
                    backorder_ship.picking_type_id,
                    self.warehouse2.out_type_id,
                    "Backorder ship should be in warehouse2",
                )
                self.assertEqual(
                    backorder_ship.location_id,
                    self.warehouse2.wh_output_stock_loc_id,
                    "Backorder ship source should be warehouse2 output",
                )

        # Add stock to warehouse2 for remaining quantity (5 serials per order)
        for backorder_data in backorder_picks:
            delivery = backorder_data["original_delivery"]
            product = delivery["product"]
            backorder_serials = []

            # Create serials for the remaining 5
            for j in range(self.SERIALS_PER_ORDER // 2, self.SERIALS_PER_ORDER):
                lot = self.env["stock.lot"].create(
                    {
                        "name": f"SO-{delivery['index']}-SN-{j+1}",
                        "product_id": product.id,
                        "company_id": self.warehouse2.company_id.id,
                    }
                )
                backorder_serials.append(lot)

                # Add stock with this serial to warehouse2
                self.env["stock.quant"]._update_available_quantity(product, self.warehouse2.lot_stock_id, 1, lot_id=lot)

            delivery["backorder_serials"] = backorder_serials

        # Assign and validate ALL backorder picks
        for backorder_data in backorder_picks:
            backorder_pick = backorder_data["pick"]
            delivery = backorder_data["original_delivery"]

            backorder_pick.action_assign()
            self.assertEqual(backorder_pick.state, "assigned", "Backorder pick should be assigned")

            # Set quantities on move lines
            for move in backorder_pick.move_ids:
                for ml in move.move_line_ids:
                    ml.quantity = ml.quantity_product_uom

            backorder_pick.button_validate()
            self.assertEqual(backorder_pick.state, "done", "Backorder pick should be done")

            # Verify serials
            pick_serials = set(backorder_pick.move_ids.move_line_ids.mapped("lot_id.name"))
            expected_serials = set(lot.name for lot in delivery["backorder_serials"])
            self.assertEqual(pick_serials, expected_serials, "Backorder pick should have expected serials")

        # Validate ALL backorder ships
        for backorder_data in backorder_ships:
            backorder_ship = backorder_data["ship"]
            delivery = backorder_data["original_delivery"]

            backorder_ship.action_assign()
            self.assertEqual(backorder_ship.state, "assigned", "Backorder ship should be assigned")

            # Verify chain links preserved
            backorder_pick = next((bp["pick"] for bp in backorder_picks if bp["original_delivery"] == delivery), None)
            if backorder_pick:
                pick_move = backorder_pick.move_ids
                ship_move = backorder_ship.move_ids
                self.assertIn(ship_move, pick_move.move_dest_ids, "Backorder ship should be linked to backorder pick")
                self.assertIn(pick_move, ship_move.move_orig_ids, "Backorder pick should be origin of backorder ship")

                # Verify serials flowed from pick to ship
                pick_serials = set(backorder_pick.move_ids.move_line_ids.mapped("lot_id.name"))
                ship_serials = set(backorder_ship.move_ids.move_line_ids.mapped("lot_id.name"))
                expected_serials = set(lot.name for lot in delivery["backorder_serials"])

                self.assertEqual(pick_serials, expected_serials, "Backorder pick should have expected serials")
                self.assertEqual(
                    ship_serials, expected_serials, "Backorder ship should have same serials as backorder pick"
                )

            # Set quantities on move lines
            for move in backorder_ship.move_ids:
                for ml in move.move_line_ids:
                    ml.quantity = ml.quantity_product_uom

            backorder_ship.button_validate()
            self.assertEqual(backorder_ship.state, "done", "Backorder ship should be done")

        # Final verification - all deliveries complete with correct serials
        for delivery in deliveries:
            pick = delivery["pick"]
            ship = delivery["ship"]

            self.assertEqual(pick.state, "done")
            self.assertEqual(ship.state, "done")

            # Find backorder pick and ship
            backorder_pick = next((bp["pick"] for bp in backorder_picks if bp["original_delivery"] == delivery), None)
            backorder_ship = next((bs["ship"] for bs in backorder_ships if bs["original_delivery"] == delivery), None)

            if backorder_pick and backorder_ship:
                self.assertEqual(backorder_pick.state, "done")
                self.assertEqual(backorder_ship.state, "done")

                # Verify all serials are accounted for
                initial_serials = set(lot.name for lot in delivery["initial_serials"])
                backorder_serials = set(lot.name for lot in delivery["backorder_serials"])

                # Get final serials from both ships
                initial_ship_serials = set(ship.move_ids.move_line_ids.mapped("lot_id.name"))
                backorder_ship_serials = set(backorder_ship.move_ids.move_line_ids.mapped("lot_id.name"))
                all_final_serials = initial_ship_serials | backorder_ship_serials

                self.assertEqual(
                    len(all_final_serials),
                    self.SERIALS_PER_ORDER,
                    f"Delivery {delivery['index']} should have all {self.SERIALS_PER_ORDER} serials",
                )
                self.assertEqual(
                    initial_serials, initial_ship_serials, f"Delivery {delivery['index']} initial serials should match"
                )
                self.assertEqual(
                    backorder_serials,
                    backorder_ship_serials,
                    f"Delivery {delivery['index']} backorder serials should match",
                )
