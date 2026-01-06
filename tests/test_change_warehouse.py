# © 2025 SJR Nebula
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


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
