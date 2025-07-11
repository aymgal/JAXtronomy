__author__ = "dangilman"

from functools import partial
from jax import config, jit

config.update("jax_enable_x64", True)

from lenstronomy.LensModel.MultiPlane.multi_plane import MultiPlane
from lenstronomy.Cosmo.background import Background
from jaxtronomy.LensModel.single_plane import SinglePlane

__all__ = ["MultiPlaneDecoupled"]

# NOTE: MultiPlane has not been implemented in JAXtronomy, so the one from lenstronomy is inherited
#       This is fine as long as only the init is needed


class MultiPlaneDecoupled(MultiPlane):
    def __init__(
        self,
        z_source,
        lens_model_list,
        lens_redshift_list,
        cosmo=None,
        observed_convention_index=None,
        ignore_observed_positions=False,
        z_source_convention=None,
        z_lens_convention=None,
        cosmo_interp=False,
        z_interp_stop=None,
        num_z_interp=100,
        profile_kwargs_list=None,
        distance_ratio_sampling=False,
        cosmology_sampling=False,
        cosmology_model="FlatLambdaCDM",
        x0_interp=None,
        y0_interp=None,
        alpha_x_interp_foreground=None,
        alpha_y_interp_foreground=None,
        alpha_x_interp_background=None,
        alpha_y_interp_background=None,
        z_split=None,
    ):
        """A class for multiplane lensing in which the deflection angles at certain
        coordinates are fixed through user-specified interpolation functions. These
        functions return fixed deflection angles that effectively decouple deflections
        by a group of deflectors at redshift Z from deflections produced by halos at
        redshift< Z.

        This class breaks the recursive nature of the multi-plane lens equation, and can
        significantly speed up computations with a large number of line-of-sight halos.

        :param lens_model_list: list of lens model strings
        :param lens_redshift_list: list of floats with redshifts of the lens models
            indicated in lens_model_list
        :param z_source_convention: float, redshift of a source to define the reduced
            deflection angles of the lens models. If None, 'z_source' is used.
        :param cosmo: instance of astropy.cosmology
        :param profile_kwargs_list: list of dicts, keyword arguments used to initialize
            profile classes in the same order of the lens_model_list. If any of the
            profile_kwargs are None, then that profile will be initialized using default
            settings.
        :param x0_interp: a function that maps an angular coordinate on the sky to the x
            coordinate of a physical position [Mpc] at the first lens plane
        :param y0_interp: same as x0_interp, but returns the y coordinate in Mpc
        :param alpha_x_interp_list: a list of functions that take as input angular
            coordinates (x, y) and returns the x-component of the deflection angle at
            each coorindate
        :param alpha_y_interp_list: same as alpha_x_interp_list, but returns the
            y-component of the deflection angle at (x,y)
        :param z_interp_list: a list of redshifts corresponding to the
            alpha_x_interp_list and alpha_y_interp_list entries
        """
        self._alphax_interp_foreground = alpha_x_interp_foreground
        self._alphay_interp_foreground = alpha_y_interp_foreground
        self._alphax_interp_background = alpha_x_interp_background
        self._alphay_interp_background = alpha_y_interp_background
        self._x0_interp = x0_interp
        self._y0_interp = y0_interp
        self._z_split = z_split
        super(MultiPlaneDecoupled, self).__init__(
            z_source=z_source,
            lens_model_list=lens_model_list,
            lens_redshift_list=lens_redshift_list,
            cosmo=cosmo,
            observed_convention_index=observed_convention_index,
            ignore_observed_positions=ignore_observed_positions,
            z_source_convention=z_source_convention,
            z_lens_convention=z_lens_convention,
            cosmo_interp=cosmo_interp,
            z_interp_stop=z_interp_stop,
            num_z_interp=num_z_interp,
            profile_kwargs_list=profile_kwargs_list,
            distance_ratio_sampling=distance_ratio_sampling,
            cosmology_sampling=cosmology_sampling,
            cosmology_model=cosmology_model,
        )

        cosmo_bkg = Background(cosmo)
        d_xy_source = cosmo_bkg.d_xy(0, z_source)
        d_xy_lens_source = cosmo_bkg.d_xy(self._z_split, z_source)
        self._reduced_to_phys = d_xy_source / d_xy_lens_source
        self._deltaT_zsplit = cosmo_bkg.d_xy(0, self._z_split)
        self._deltaT_zsplit_zsource = cosmo_bkg.T_xy(self._z_split, z_source)
        self._Ts = cosmo_bkg.T_xy(0, z_source)
        self._Td = cosmo_bkg.T_xy(0, z_split)
        self._Tds = cosmo_bkg.T_xy(self._z_split, z_source)
        self._main_deflector = SinglePlane(
            lens_model_list, profile_kwargs_list=profile_kwargs_list
        )
        # useful to have these saved to access later outside the class
        self.kwargs_multiplane_model = {
            "x0_interp": self._x0_interp,
            "y0_interp": self._y0_interp,
            "alpha_x_interp_foreground": self._alphax_interp_foreground,
            "alpha_y_interp_foreground": self._alphay_interp_foreground,
            "alpha_x_interp_background": self._alphax_interp_background,
            "alpha_y_interp_background": self._alphay_interp_background,
            "z_split": self._z_split,
        }

    def geo_shapiro_delay(*args, **kwargs):
        raise Exception(
            "time delays are not yet implemented for the MultiPlaneDecoupled class"
        )

    def ray_shooting_partial_comoving(self, *args, **kwargs):
        raise Exception(
            "ray_shooting_partial_comoving is not well defined for this class"
        )

    @partial(jit, static_argnums=0)
    def ray_shooting(self, theta_x, theta_y, kwargs_lens, *args, **kwargs):
        """Ray-shooting through the lens volume with fixed deflection angles at certain
        lens planes passed through the alpha_x_interp/alpha_y_interp lists. Starts with
        (x,y) co-moving distance passed through the x0_interp and y0_interp functions,
        then starts multi-plane ray-tracing through all subsequent lens planes.

        :param theta_x: angular coordinate on the sky
        :param theta_y: angular coordinate on the sky
        :param kwargs_lens: keyword arguments for the main deflector
        :return: coordinates on the source plane
        """
        coordinates = (theta_x, theta_y)
        # here we use an interpolation function to compute the comoving coordinates of the light rays
        # where they hit the main lens plane at redshift z = z_main
        x = self._x0_interp(coordinates)
        y = self._y0_interp(coordinates)

        theta_x_main = x / self._Td  # the angular coordinates of the ray positions
        theta_y_main = y / self._Td  # the angular coordinates of the ray positions

        # now we compute (via the interpolation functions) the deflection angles from all deflectors at z <= z_main, \
        # exlucding the main deflector
        angle_x_foreground = self._alphax_interp_foreground(coordinates)
        angle_y_foreground = self._alphay_interp_foreground(coordinates)

        # compute the deflection angles from the main deflector
        deflection_x_main, deflection_y_main = self._main_deflector.alpha(
            theta_x_main, theta_y_main, kwargs_lens
        )

        deflection_x_main *= self._reduced_to_phys
        deflection_y_main *= self._reduced_to_phys

        # add the main deflector to the deflection field
        angle_x = angle_x_foreground - deflection_x_main
        angle_y = angle_y_foreground - deflection_y_main

        # now we compute (via the interpolation functions) the deflection angles from all deflectors at z > z_main
        deflection_x_background = self._alphax_interp_background(coordinates)
        deflection_y_background = self._alphay_interp_background(coordinates)

        # combine deflections
        alpha_background_x = angle_x + deflection_x_background
        alpha_background_y = angle_y + deflection_y_background

        # ray propagation to the source plane with the small angle approximation
        beta_x = x / self._Ts + alpha_background_x * self._Tds / self._Ts
        beta_y = y / self._Ts + alpha_background_y * self._Tds / self._Ts

        return beta_x, beta_y

    @partial(jit, static_argnums=0)
    def alpha(self, theta_x, theta_y, kwargs_lens, *args, **kwargs):
        """Reduced deflection angle.

        :param theta_x: angle in x-direction
        :param theta_y: angle in y-direction
        :param kwargs_lens: lens model kwargs
        :return: deflection angles in x and y directions
        """
        beta_x, beta_y = self.ray_shooting(
            theta_x,
            theta_y,
            kwargs_lens,
        )

        alpha_x = theta_x - beta_x
        alpha_y = theta_y - beta_y

        return alpha_x, alpha_y

    @partial(jit, static_argnums=0)
    def hessian(self, theta_x, theta_y, kwargs_lens, diff=0.00000001, *args, **kwargs):
        """Computes the hessian components f_xx, f_yy, f_xy from f_x and f_y with
        numerical differentiation.

        :param theta_x: x-position (preferentially arcsec)
        :type theta_x: numpy array
        :param theta_y: y-position (preferentially arcsec)
        :type theta_y: numpy array
        :param kwargs_lens: list of keyword arguments of lens model parameters matching
            the lens model classes
        :param diff: numerical differential step (float)
        :return: f_xx, f_xy, f_yx, f_yy
        """
        alpha_ra, alpha_dec = self.alpha(
            theta_x,
            theta_y,
            kwargs_lens,
        )

        alpha_ra_dx, alpha_dec_dx = self.alpha(
            theta_x + diff,
            theta_y,
            kwargs_lens,
        )
        alpha_ra_dy, alpha_dec_dy = self.alpha(
            theta_x,
            theta_y + diff,
            kwargs_lens,
        )

        dalpha_rara = (alpha_ra_dx - alpha_ra) / diff
        dalpha_radec = (alpha_ra_dy - alpha_ra) / diff
        dalpha_decra = (alpha_dec_dx - alpha_dec) / diff
        dalpha_decdec = (alpha_dec_dy - alpha_dec) / diff

        f_xx = dalpha_rara
        f_yy = dalpha_decdec
        f_xy = dalpha_radec
        f_yx = dalpha_decra
        return f_xx, f_xy, f_yx, f_yy
