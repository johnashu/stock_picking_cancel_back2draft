==========================================
Stock Picking Cancel, Draft & Change Warehouse
==========================================

.. |badge1| image:: https://img.shields.io/badge/licence-AGPL--3-blue.png
    :target: http://www.gnu.org/licenses/agpl-3.0-standalone.html
    :alt: License: AGPL-3

|badge1|

This module allows you to change the warehouse on pickings with a single click.
The "Change Warehouse" action will automatically:

1. Cancel the picking (if not already cancelled)
2. Reset it to draft state
3. Update the warehouse, operation type, and locations
4. Confirm the picking as "To Do"

This is especially useful for 2-step or 3-step delivery/receipt workflows where
you need to reassign pickings to a different warehouse while preserving the
chain links between Pick and Out operations.

**Table of contents**

.. contents::
   :local:

Installation
============

1. Copy the ``stock_picking_cancel_back2draft`` folder to your Odoo addons directory.
2. Update the apps list in Odoo (Settings > Apps > Update Apps List).
3. Search for "Stock Picking Cancel, Draft & Change Warehouse" and install it.

Configuration
=============

To allow users to change warehouse on pickings, you must add them to the
"Cancel & Back to Draft Pickings" security group:

1. Go to Settings > Users & Companies > Users.
2. Select the user you want to grant access to.
3. Under "Other" or "Technical Settings", enable "Cancel & Back to Draft Pickings".

Only users in this group will see the button and be able to use this feature.

Usage
=====

To change the warehouse on a picking:

1. Open a picking form view (any state except "Done")
2. Click the **"Change Warehouse"** button
3. In the wizard:
   - Select the new warehouse
   - Choose whether to include chained pickings (Pick/Out chain)
   - Review the pickings that will be updated
4. Click **"Change Warehouse"** to execute

The system will automatically:

- Cancel the pickings (preserving chain links)
- Reset them to draft
- Update warehouse, operation type, and locations
- Confirm them as "To Do"

**Note:** This action cannot be performed on pickings in the "Done" state.

Features
========

- **Single-click operation**: No need to manually cancel and reset pickings first
- **Chain preservation**: Pick <-> Out chain links are maintained
- **Serial number flow**: Serial numbers flow correctly through the chain after warehouse change
- **Automatic confirmation**: Pickings are marked as "To Do" after the change
- **Chained picking support**: Optionally update all linked pickings in the chain
- **Multi-step delivery support**: Works with 2-step (Pick + Ship) and 3-step workflows
- **Multi-step receipt support**: Works with 2-step (Receipt + Internal) workflows

Running Tests
=============

To run the module tests in a Docker container:

.. code-block:: bash

    # Using docker exec (on a running container)
    docker exec -it <container_id> odoo \
      --test-enable \
      --stop-after-init \
      -d odoo \
      -u stock_picking_cancel_back2draft \
      --test-tags /stock_picking_cancel_back2draft

    # Using docker compose run (creates a new container)
    docker compose run --rm odoo odoo --test-enable --stop-after-init \
      -d odoo \
      -u stock_picking_cancel_back2draft \
      --log-level=test

Replace ``<container_id>`` with your Odoo container ID and adjust database settings as needed.

Test flags:

- ``--test-enable`` - Enable test mode
- ``--stop-after-init`` - Stop after running tests
- ``-d`` - Database name
- ``-u`` - Update/test this module (use ``-i`` if not installed yet)
- ``--log-level=test`` - Show test output

Support
=======

For support, please contact SJR Nebula:

- Website: https://sjr.ie
- Email: info@sjr.ie

Credits
=======

Authors
-------

* SJR Nebula

Contributors
------------

* John Ashurst
