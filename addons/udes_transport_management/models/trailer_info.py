# -*- coding: utf-8 -*-

from odoo import api, fields, models

class TrailerInfo(models.Model):

    _name = 'udes_transport_management.trailer_info'
    _description = 'UDES Trailer Info'

    trailer_num = fields.Integer(string='Trailer number',
                                 required=False, copy=False)
    trailer_ident = fields.Char(string='Trailer unit ID',
                                required=False, copy=False)
    trailer_license = fields.Char(string='Vehicle registration',
                                  required=False, copy=False)
    trailer_driver = fields.Char(string='Driver name',
                                 required=False, copy=False)

    picking_id = fields.Many2one('stock.picking',
                                 'Transfer',
                                 required=True,
                                 index=True,
                                 copy=False)

    _sql_constraints = [
        ('picking_id_uniq', 'unique(picking_id)', 'Only one trailer information is allowed per picking.'),
    ]

    def _update_picking_data(self):
        """ Update u_trailer_info_id of the related picking if
            it is not set at the picking.
        """
        self.ensure_one()
        from_picking = self.env.context.get('from_picking', False)
        if not self.picking_id.u_trailer_info_id:
            self.picking_id.u_trailer_info_id = self

    @api.model
    def create(self, values):
        res = super(TrailerInfo, self).create(values)
        res._update_picking_data()
        return res