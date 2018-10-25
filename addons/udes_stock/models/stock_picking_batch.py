# -*- coding: utf-8 -*-

from odoo import api, models, _
from odoo.exceptions import ValidationError


class StockPickingBatch(models.Model):
    _inherit = 'stock.picking.batch'

    def get_next_task(self, skipped_product_ids=None):
        """ Gets the next not completed task of the batch to be done
        """
        self.ensure_one()

        mls = self.get_available_move_lines(
            state='not_done',
            skipped_product_ids=skipped_product_ids, sorted=True)
        tasks_picked = len(self.get_available_move_lines().filtered(
            lambda ml: ml.qty_done == ml.product_qty)) > 0
        task = {'tasks_picked': tasks_picked,
                'num_tasks_to_pick': 0,
                'move_line_ids': []}

        if mls:
            group_key = lambda ml: (ml.package_id.id,
                                    ml.product_id.id,
                                    ml.location_id.id)
            grouped_mls = mls.groupby(group_key)
            _key, task_mls = next(grouped_mls)
            next_ml = task_mls[0]

            # NB(ale): this is how we add all the MLs state to the response
            task.update(task_mls._prepare_task_info())

            user_scans = next_ml.picking_id.picking_type_id.u_user_scans

            if user_scans == 'product':
                task['num_tasks_to_pick'] = len(list(grouped_mls)) + 1
                task['move_line_ids'] = task_mls.ids
            else:
                # TODO: check pallets
                task['num_tasks_to_pick'] = len(mls.mapped('package_id'))
                task['move_line_ids'] = mls.filtered(
                    lambda ml: ml.package_id == next_ml.package_id).ids

        return task

    def _check_user_id(self, user_id):
        if user_id is None:
            user_id = self.env.user.id

        if not user_id:
            raise ValidationError(_("Cannot determine the user."))

        return user_id

    @api.multi
    def get_single_batch(self, user_id=None):
        """
        Search for a picking batch in progress for the specified user.
        If no user is specified, the current user is considered.
        If no batch is found, but pickings exist, create a new batch.

        If a batch is determined, return it, otherwise return None.

        Raise a ValidationError in case it cannot perform a search
        or if multiple batches are found for the specified user.
        """
        PickingBatch = self.env['stock.picking.batch']

        user_id = self._check_user_id(user_id)
        batches = PickingBatch.search([('user_id', '=', user_id),
                                       ('state', '=', 'in_progress')])
        batch = None

        if batches:
            if len(batches) > 1:
                raise ValidationError(
                    _("Found %d batches for the user, please contact "
                      "administrator.") % len(batches))

            batch = batches

        return batch

    def _prepare_info(self, allowed_picking_states):
        pickings = self.picking_ids

        if allowed_picking_states:
            pickings = pickings.filtered(
                lambda x: x.state in allowed_picking_states)

        return {'id': self.id,
                'picking_ids': pickings.get_info()}

    def get_info(self, allowed_picking_states):
        """
        Return list of dictionaries containing information about
        all batches.
        """
        return [batch._prepare_info(allowed_picking_states)
                for batch in self]

    @api.multi
    def create_batch(self, picking_type_id, picking_priorities, user_id=None):
        """
        Creeate and return a batch for the specified user if pickings
        exist. Return None otherwise. Pickings are filtered based on
        the specified picking priorities (list of int strings, for
        example ['2', '3']).

        If the user already has batches assigned, a ValidationError
        is raised in case of pickings that need to be completed,
        otherwise such batches will be marked as done.
        """
        user_id = self._check_user_id(user_id)
        self._check_batches(user_id)

        return self._create_batch(user_id, picking_type_id, picking_priorities)

    def _create_batch(self, user_id, picking_type_id, picking_priorities=None):
        """
        Create a batch for the specified user by including only
        those pickings with the specified picking_type_id and picking
        priorities (optional).
        In case no pickings exist, return None.
        """
        Picking = self.env['stock.picking']
        PickingBatch = self.env['stock.picking.batch']

        search_domain = []

        if picking_priorities is not None:
            search_domain.append(('priority', 'in', picking_priorities))

        search_domain.extend([('picking_type_id', '=', picking_type_id),
                              ('state', '=', 'assigned'),
                              ('batch_id', '=', False)])

        picking = Picking.search(search_domain,
                                 order='priority desc, scheduled_date, id',
                                 limit=1)

        if not picking:
            return None

        batch = PickingBatch.create({'user_id': user_id})
        picking.batch_id = batch.id
        batch.write({'state': 'in_progress'})

        return batch

    def _check_batches(self, user_id, batches=None, raise_error=True):
        """
        Checks if batches are complete, and marks as done.

        If no batch is provided, searches all batches in progress
        for the specified user.

        Optionally raises a ValidationError if any non-draft picking
        is incomplete.
        """
        Picking = self.env['stock.picking']
        PickingBatch = self.env['stock.picking.batch']

        if not batches:
            batches = PickingBatch.search([('user_id', '=', user_id),
                                           ('state', '=', 'in_progress')])

        if batches:
            not_ready_picks = Picking.browse()
            incomplete_picks = Picking.browse()

            for pick in batches.mapped('picking_ids'):
                if pick.state in ['draft', 'waiting', 'confirmed']:
                    not_ready_picks += pick
                elif pick.state not in ['done', 'cancel']:
                    incomplete_picks += pick

            if not_ready_picks:
                not_ready_picks.write({'batch_id': None})

            if incomplete_picks and raise_error:
                picks_txt = ','.join([x.name for x in incomplete_picks])
                raise ValidationError(
                    _("The user already has pickings that need completing - "
                      "please complete those before requesting "
                      "more:\n {}".format(picks_txt)))

            # all the picks in the waves are finished
            if not incomplete_picks:
                batches.done()

    def drop_off_picked(self, continue_batch, location_barcode):
        """
        Validate the move lines of the batch (expects a singleton)
        by moving them to the specified location.

        In case continue_batch is flagged, mark the batch as
        'done' if appropriate.
        """
        self.ensure_one()

        if self.state != 'in_progress':
            raise ValidationError(_("Wrong batch state: %s.") % self.state)

        Location = self.env['stock.location']
        dest_loc = None

        if location_barcode:
            dest_loc = Location.get_location(location_barcode)

        completed_move_lines = self.picking_ids.mapped('move_line_ids').filtered(
            lambda x: x.qty_done > 0
                      and x.picking_id.state not in ['cancel', 'done'])

        if dest_loc and completed_move_lines:
            completed_move_lines.write({'location_dest_id': dest_loc.id})

        if completed_move_lines:
            pickings = completed_move_lines.mapped('picking_id')

            for pick in pickings:
                pick_todo = pick
                pick_mls = completed_move_lines.filtered(lambda x: x.picking_id == pick)
                if pick._requires_backorder(pick_mls):
                    pick_todo = pick._backorder_movelines(pick_mls)
                # at this point pick_todo should contain only mls done
                pick_todo.update_picking(validate=True)

        incomplete_picks = self.picking_ids.filtered(
            lambda x: x.state not in ['done', 'cancel'])

        if not continue_batch:
            incomplete_picks.write({'batch_id': False})

        return self

    def is_valid_location_dest_id(self, location_ref):
        """
        Whether the specified location (via ID, name or barcode)
        is a valid putaway location for the all the relevant
        pickings of the batch.
        Expects a singleton instance.

        Returns a boolean indicating the validity check outcome.
        """
        self.ensure_one()

        Location = self.env['stock.location']
        location = None

        try:
            location = Location.get_location(location_ref)
        except:
            return False

        done_pickings = self.picking_ids.filtered(
            lambda p: p.state == 'assigned')
        done_move_lines = done_pickings.get_move_lines_done()
        all_done_pickings = done_move_lines.mapped('picking_id')

        return all([pick.is_valid_location_dest_id(location=location)
                    for pick in all_done_pickings])

    def unpickable_item(self, reason, product_id=None, location_id=None,
                        package_name=None, picking_type_id=None, lot_name=None):
        """
        Given an unpickable product or package, find the related
        move lines in the current wave, backorder them and refine
        the backorder (by default it is canceled).
        Then create a new picking of type picking_type_id for the unpickable stock.
        If the picking was the last one of the wave, the wave is set as done.

        An unpickable product requires at least the location_id and
        optionally the package_id and lot_name.
        """
        self.ensure_one()

        ResUsers = self.env['res.users']
        Picking = self.env['stock.picking']
        Group = self.env['procurement.group']
        Package = self.env['stock.quant.package']
        Location = self.env['stock.location']
        Product = self.env['product.product']

        product = Product.get_product(product_id) if product_id else None
        location = Location.get_location(location_id) if location_id else None
        package = Package.get_package(package_name) if package_name else None
        allow_partial = False
        move_lines = self.get_available_move_lines()

        if product:
            if not location:
                raise ValidationError(
                    _('Missing location parameter for unpickable product %s.') %
                    product.name)

            move_lines = move_lines.filtered(lambda ml: ml.product_id == product and
                                             ml.location_id == location)
            msg = _('Unpickable product %s at location %s') % (product.name, location.name)

            if lot_name:
                move_lines = move_lines.filtered(lambda ml: ml.lot_id.name == lot_name)
                msg += _(' with serial number %s') % lot_name

            if package:
                move_lines = move_lines.filtered(lambda ml: ml.package_id == package)
                msg += _(' in package %s') % package.name
            elif move_lines.mapped('package_id'):
                raise ValidationError(
                    _('Unpickable product from a package but no package name provided.'))

            # at this point we should only have one move_line
            quants = move_lines.get_quants()
            allow_partial = True
        elif package:
            if not location:
                location=package.location_id

            move_lines = move_lines.filtered(lambda ml: ml.package_id == package)
            quants = package._get_contained_quants()
            msg = _('Unpickable package %s at location %s') % (package.name, location.name)
        else:
            raise ValidationError(
                _('Missing required information for unpickable item: product or package.'))

        if not move_lines:
            raise ValidationError(
                _('Cannot find move lines todo for unpickable item '
                  'in this batch.'))

        # at this point we should have only one picking_id
        picking = move_lines.mapped('picking_id')
        picking.message_post(body=msg)

        if picking_type_id is None:
            picking_type_id = ResUsers.get_user_warehouse().int_type_id.id

        if picking.batch_id != self:
            raise ValidationError(_('Move line is not part of the batch.'))

        if picking.state in ['cancel', 'done']:
            raise ValidationError(_('Cannot mark a move line as unpickable '
                                    'when it is part of a completed Picking.'))

        original_picking_id = None

        if len(picking.move_line_ids - move_lines):
            # Create a backorder for the affected move lines if
            # there are move lines that are not affected
            original_picking_id = picking.id
            picking = picking._backorder_movelines(move_lines)

        # By default the pick is unreserved
        picking._refine_picking(reason)

        group = Group.get_group(group_identifier=reason,
                                create=True)

        # create new "investigation pick"
        Picking.with_context(allow_partial=allow_partial)\
               .create_picking(quant_ids=quants.mapped('id'),
                               location_id=location.id,
                               picking_type_id=picking_type_id,
                               group_id=group.id)

        # Try to re-assing the picking after, by creating the
        # investigation, we've reserved the problematic stock
        picking.action_assign()

        if picking.state != 'assigned':
            # Remove the picking from the batch as it cannot be
            # processed for lack of stock; we do so to be able
            # to terminate the batch and let the user create a
            # new batch for himself
            picking.batch_id = False
        elif original_picking_id is not None:
            # A backorder has been created, but the stock is
            # available; get rid of the backorder after linking the
            # move lines to the original picking, so it can be
            # directly processed
            picking.move_line_ids.write({'picking_id': original_picking_id})
            picking.move_lines.write({'picking_id': original_picking_id})
            picking.unlink()

        # If the batch does not contain any remaining picking to do,
        # it can be set as done
        remaining_pickings = self.picking_ids.filtered(
            lambda x: x.state in ['assigned'])

        if not remaining_pickings.exists():
            self.state = 'done'

        return True

    def get_available_move_lines(self, state=None, skipped_product_ids=None, sorted=False):
        """ Get all the move lines from available pickings
        """
        self.ensure_one()

        available_pickings = self.picking_ids.filtered(lambda p: p.state == 'assigned')

        mls = available_pickings.mapped('move_line_ids')

        if skipped_product_ids:
            mls = mls.filtered(lambda ml: ml.product_id.id not in skipped_product_ids)

        if sorted:
            mls = mls.sort_by_location_product(state=state)

        return mls

    @api.one
    def check_batches(self):
        """ If picking IDs are updated on an in progress batch, check if it is now complete
        """
        if self.state == 'in_progress':
            self._check_batches(None, batches=self, raise_error=False)

    def write(self, vals):
        """ If writing batch, check if now complete
        """
        res = super(StockPickingBatch, self).write(vals)
        self.check_batches()
        return res

    def get_user_batches(self):
        """ Get all batches for user
        """
        # Search for in progress batches
        batches = self.search([('user_id', '=', self.env.user.id),
                               ('state', '=', 'in_progress')])
        return batches

    def unassign_user_batches(self):
        """ Removes pickings from all batches assigned to the user and cancels the batch
        """
        # Get user batches
        batches = self.get_user_batches()
        
        # Unassign batch_id from incomplete stock pickings
        batches.mapped('picking_ids').filtered(lambda sp: sp.state == 'assigned').write({'batch_id': False})
