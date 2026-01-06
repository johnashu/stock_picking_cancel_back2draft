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
        cls.partner = cls.env.ref("base.res_partner_2")

        # Find an existing storable product
        cls.product = cls.env["product.product"].search(
            [("detailed_type", "=", "product")], limit=1
        )
        if not cls.product:
            cls.product = cls.env["product.product"].create({
                "name": "Test Storable Product",
                "detailed_type": "product",
            })

        # Add the security group to the admin user
        cls.group_cancel_draft = cls.env.ref(
            "stock_picking_cancel_back2draft.group_stock_picking_cancel_back2draft"
        )
        cls.env.user.groups_id = [(4, cls.group_cancel_draft.id)]

        # Get or create two warehouses with 2-step delivery
        cls.warehouse1 = cls.env["stock.warehouse"].search([], limit=1)
        cls.warehouse1.delivery_steps = "pick_ship"

        # Create second warehouse
        cls.warehouse2 = cls.env["stock.warehouse"].search([
            ("id", "!=", cls.warehouse1.id)
        ], limit=1)
        if not cls.warehouse2:
            cls.warehouse2 = cls.env["stock.warehouse"].create({
                "name": "Test Warehouse 2",
                "code": "WH2",
            })
        cls.warehouse2.delivery_steps = "pick_ship"

    def _create_two_step_delivery(self, warehouse):
        """Create a 2-step delivery chain using procurement (like a Sale Order)."""
        procurement_group = self.env["procurement.group"].create({
            "name": "Test Sale Order",
        })

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

        ship_picking = self.env["stock.picking"].search([
            ("group_id", "=", procurement_group.id),
            ("picking_type_id", "=", warehouse.out_type_id.id),
        ])
        pick_picking = self.env["stock.picking"].search([
            ("group_id", "=", procurement_group.id),
            ("picking_type_id", "=", warehouse.pick_type_id.id),
        ])

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
        wizard = self.env["stock.picking.change.warehouse"].with_context(
            active_ids=[pick.id],
            active_model="stock.picking",
        ).create({
            "new_warehouse_id": self.warehouse2.id,
            "include_chained_pickings": True,
        })

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
        wizard = self.env["stock.picking.change.warehouse"].with_context(
            active_ids=[pick.id],
            active_model="stock.picking",
        ).create({
            "new_warehouse_id": self.warehouse2.id,
            "include_chained_pickings": True,
        })
        wizard.action_change_warehouse()

        # Verify sale_line_id preserved
        self.assertEqual(pick.move_ids.sale_line_id.id, fake_sale_line_id)
        self.assertEqual(ship.move_ids.sale_line_id.id, fake_sale_line_id)

    def test_change_warehouse_preserves_procurement_group(self):
        """Test that changing warehouse preserves the procurement group."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        original_group = pick.group_id

        # Change warehouse (will auto-cancel and reset)
        wizard = self.env["stock.picking.change.warehouse"].with_context(
            active_ids=[pick.id],
            active_model="stock.picking",
        ).create({
            "new_warehouse_id": self.warehouse2.id,
            "include_chained_pickings": True,
        })
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
        wizard = self.env["stock.picking.change.warehouse"].with_context(
            active_ids=[pick.id],
            active_model="stock.picking",
        ).create({
            "new_warehouse_id": self.warehouse2.id,
            "include_chained_pickings": True,
        })
        wizard.action_change_warehouse()

        # Verify pickings were updated and confirmed
        self.assertEqual(pick.picking_type_id, self.warehouse2.pick_type_id)
        self.assertIn(pick.state, ("confirmed", "assigned", "waiting"))

    def test_change_warehouse_without_chained(self):
        """Test changing warehouse without including chained pickings."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Create wizard without chained pickings (on confirmed picking)
        wizard = self.env["stock.picking.change.warehouse"].with_context(
            active_ids=[pick.id],
            active_model="stock.picking",
        ).create({
            "new_warehouse_id": self.warehouse2.id,
            "include_chained_pickings": False,
        })

        # Verify only pick is included (not ship)
        self.assertEqual(wizard.picking_count, 1)

    def test_change_warehouse_fails_on_done_picking(self):
        """Test that changing warehouse fails on done pickings."""
        pick, ship = self._create_two_step_delivery(self.warehouse1)

        # Add stock and complete the pick
        self.env["stock.quant"]._update_available_quantity(
            self.product, self.warehouse1.lot_stock_id, 10
        )
        pick.action_assign()
        for move in pick.move_ids:
            move.quantity = move.product_uom_qty
        pick.button_validate()

        self.assertEqual(pick.state, "done")

        # Try to open wizard on done picking - should fail
        with self.assertRaises(UserError):
            pick.action_open_change_warehouse_wizard()

