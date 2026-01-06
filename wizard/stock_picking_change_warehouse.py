from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPickingChangeWarehouse(models.TransientModel):
    _name = "stock.picking.change.warehouse"
    _description = "Change Warehouse for Pickings"

    picking_ids = fields.Many2many(
        "stock.picking",
        "stock_picking_change_wh_picking_rel",
        string="Pickings",
        readonly=True,
    )
    current_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Current Warehouse",
        readonly=True,
    )
    new_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="New Warehouse",
        required=True,
        domain="[('id', '!=', current_warehouse_id), ('company_id', '=', company_id)]",
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        readonly=True,
        default=lambda self: self.env.company,
    )
    include_chained_pickings = fields.Boolean(
        string="Include Chained Pickings",
        default=True,
        help="Also change warehouse for linked Pick/Out operations in the chain",
    )
    chained_picking_ids = fields.Many2many(
        "stock.picking",
        "stock_picking_change_wh_chained_rel",
        string="Chained Pickings",
        compute="_compute_chained_pickings",
        readonly=True,
    )
    picking_count = fields.Integer(
        string="Pickings to Update",
        compute="_compute_chained_pickings",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        active_ids = self.env.context.get("active_ids", [])
        if active_ids:
            pickings = self.env["stock.picking"].browse(active_ids)
            res["picking_ids"] = [(6, 0, pickings.ids)]
            # Get the warehouse from the first picking
            warehouses = pickings.mapped("picking_type_id.warehouse_id")
            if len(warehouses) == 1:
                res["current_warehouse_id"] = warehouses.id
            # Get company from pickings
            companies = pickings.mapped("company_id")
            if len(companies) == 1:
                res["company_id"] = companies.id
        return res

    @api.depends("picking_ids", "include_chained_pickings")
    def _compute_chained_pickings(self):
        for wizard in self:
            if wizard.include_chained_pickings:
                all_pickings = wizard._get_all_chained_pickings(wizard.picking_ids)
            else:
                all_pickings = wizard.picking_ids
            wizard.chained_picking_ids = all_pickings
            wizard.picking_count = len(all_pickings)

    def _get_all_chained_pickings(self, pickings):
        """Get all pickings in the chain (both upstream and downstream)."""
        all_pickings = pickings
        moves = pickings.mapped("move_ids")

        # Get downstream pickings (move_dest_ids)
        dest_moves = moves.mapped("move_dest_ids")
        while dest_moves:
            dest_pickings = dest_moves.mapped("picking_id")
            # Check if there are any NEW pickings not already in our set
            new_pickings = dest_pickings - all_pickings
            if new_pickings:
                all_pickings |= new_pickings
                dest_moves = new_pickings.mapped("move_ids.move_dest_ids")
            else:
                break

        # Get upstream pickings (move_orig_ids)
        orig_moves = moves.mapped("move_orig_ids")
        while orig_moves:
            orig_pickings = orig_moves.mapped("picking_id")
            # Check if there are any NEW pickings not already in our set
            new_pickings = orig_pickings - all_pickings
            if new_pickings:
                all_pickings |= new_pickings
                orig_moves = new_pickings.mapped("move_ids.move_orig_ids")
            else:
                break

        return all_pickings

    def action_change_warehouse(self):
        """Change warehouse for selected pickings and optionally chained pickings."""
        self.ensure_one()

        if not self.new_warehouse_id:
            raise UserError(_("Please select a new warehouse."))

        if self.new_warehouse_id == self.current_warehouse_id:
            raise UserError(_("New warehouse must be different from current warehouse."))

        pickings = self.chained_picking_ids if self.include_chained_pickings else self.picking_ids

        # Validate all pickings are in draft or cancel state
        invalid_pickings = pickings.filtered(lambda p: p.state not in ("draft", "cancel"))
        if invalid_pickings:
            raise UserError(
                _(
                    "All pickings must be in 'Draft' or 'Cancelled' state. "
                    "Please use 'Cancel & Back to Draft' first.\n\n"
                    "Invalid pickings: %s"
                )
                % ", ".join(invalid_pickings.mapped("name"))
            )

        # Change warehouse for each picking
        for picking in pickings:
            self._update_picking_warehouse(picking, self.new_warehouse_id)

        # Return action to show updated pickings
        if len(pickings) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "stock.picking",
                "view_mode": "form",
                "res_id": pickings.id,
                "target": "current",
            }
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "view_mode": "list,form",
            "domain": [("id", "in", pickings.ids)],
            "target": "current",
            "name": _("Updated Pickings"),
        }

    def _update_picking_warehouse(self, picking, new_warehouse):
        """Update a picking's warehouse, locations, and operation type."""
        old_picking_type = picking.picking_type_id
        new_picking_type = self._get_equivalent_picking_type(old_picking_type, new_warehouse)

        if not new_picking_type:
            raise UserError(
                _(
                    "Could not find equivalent operation type in warehouse '%(warehouse)s' "
                    "for operation '%(operation)s'.",
                    warehouse=new_warehouse.name,
                    operation=old_picking_type.name,
                )
            )

        # Determine new locations based on picking type
        new_location_id = self._get_new_source_location(picking, new_picking_type, new_warehouse)
        new_location_dest_id = self._get_new_dest_location(picking, new_picking_type, new_warehouse)

        # Update picking
        picking.write({
            "picking_type_id": new_picking_type.id,
            "location_id": new_location_id.id,
            "location_dest_id": new_location_dest_id.id,
        })

        # Update moves
        for move in picking.move_ids:
            move_vals = {
                "picking_type_id": new_picking_type.id,
                "warehouse_id": new_warehouse.id,
                "location_id": new_location_id.id,
                "location_dest_id": new_location_dest_id.id,
            }
            move.write(move_vals)

    def _get_equivalent_picking_type(self, old_picking_type, new_warehouse):
        """Find the equivalent picking type in the new warehouse."""
        # Map picking types by their code (incoming, outgoing, internal)
        code = old_picking_type.code

        # Try to find by sequence code pattern (e.g., PICK, OUT, IN)
        sequence_code = old_picking_type.sequence_code

        # First try exact match by sequence code
        new_picking_type = self.env["stock.picking.type"].search([
            ("warehouse_id", "=", new_warehouse.id),
            ("sequence_code", "=", sequence_code),
        ], limit=1)

        if new_picking_type:
            return new_picking_type

        # Fall back to matching by operation type code
        new_picking_type = self.env["stock.picking.type"].search([
            ("warehouse_id", "=", new_warehouse.id),
            ("code", "=", code),
        ], limit=1)

        return new_picking_type

    def _get_new_source_location(self, picking, new_picking_type, new_warehouse):
        """Determine the new source location based on picking type and context."""
        old_location = picking.location_id

        # If source is supplier location, keep it (for receipts)
        if old_location.usage == "supplier":
            return old_location

        # If source is customer location, keep it (for returns)
        if old_location.usage == "customer":
            return old_location

        # For internal moves, map to new warehouse locations
        if new_picking_type.code == "internal":
            # Check if this is a Pick operation (from stock to output)
            if old_location == picking.picking_type_id.warehouse_id.lot_stock_id:
                return new_warehouse.lot_stock_id
            # Check if this is from output location
            if old_location == picking.picking_type_id.warehouse_id.wh_output_stock_loc_id:
                return new_warehouse.wh_output_stock_loc_id
            # Check if this is from input location
            if old_location == picking.picking_type_id.warehouse_id.wh_input_stock_loc_id:
                return new_warehouse.wh_input_stock_loc_id

        # For outgoing (delivery), source should be output or stock
        if new_picking_type.code == "outgoing":
            # 2-step: Out is from output location
            if new_warehouse.delivery_steps in ("pick_ship", "pick_pack_ship"):
                return new_warehouse.wh_output_stock_loc_id
            # 1-step: directly from stock
            return new_warehouse.lot_stock_id

        # For incoming (receipt), source is typically supplier
        if new_picking_type.code == "incoming":
            return old_location  # Keep supplier location

        # Default to the new picking type's default source
        return new_picking_type.default_location_src_id or new_warehouse.lot_stock_id

    def _get_new_dest_location(self, picking, new_picking_type, new_warehouse):
        """Determine the new destination location based on picking type and context."""
        old_location = picking.location_dest_id

        # If destination is customer location, keep it (for deliveries)
        if old_location.usage == "customer":
            return old_location

        # If destination is supplier location, keep it (for returns to supplier)
        if old_location.usage == "supplier":
            return old_location

        # For internal moves (like Pick operation in 2-step)
        if new_picking_type.code == "internal":
            # Check if this is a Pick operation (to output)
            if old_location == picking.picking_type_id.warehouse_id.wh_output_stock_loc_id:
                return new_warehouse.wh_output_stock_loc_id
            # Check if going to stock
            if old_location == picking.picking_type_id.warehouse_id.lot_stock_id:
                return new_warehouse.lot_stock_id
            # Check if going to pack location (3-step)
            if old_location == picking.picking_type_id.warehouse_id.wh_pack_stock_loc_id:
                return new_warehouse.wh_pack_stock_loc_id

        # For outgoing, destination is customer
        if new_picking_type.code == "outgoing":
            return old_location  # Keep customer location

        # For incoming (receipt), destination depends on receipt steps
        if new_picking_type.code == "incoming":
            # 2-step or 3-step receipt: goes to input first
            if new_warehouse.reception_steps in ("two_steps", "three_steps"):
                return new_warehouse.wh_input_stock_loc_id
            # 1-step: directly to stock
            return new_warehouse.lot_stock_id

        # Default to the new picking type's default destination
        return new_picking_type.default_location_dest_id or new_warehouse.lot_stock_id

