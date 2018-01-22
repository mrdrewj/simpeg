from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from SimPEG import Utils
from SimPEG.EM.Static.DC.FieldsDC_2D import (
    Fields_ky, Fields_ky_CC, Fields_ky_N
    )
from SimPEG.EM.Static.DC import BaseDCProblem_2D, Problem2D_CC, Problem2D_N
import numpy as np
from SimPEG.Utils import Zero
from SimPEG.EM.Static.DC import getxBCyBC_CC
from .SurveyIP import Survey
from SimPEG import Props
from scipy.special import kn


class BaseIPProblem_2D(BaseDCProblem_2D):

    sigma = Props.PhysicalProperty(
        "Electrical conductivity (S/m)"
    )

    rho = Props.PhysicalProperty(
        "Electrical resistivity (Ohm m)"
    )

    Props.Reciprocal(sigma, rho)

    eta, etaMap, etaDeriv = Props.Invertible(
        "Electrical Chargeability (V/V)"
    )

    surveyPair = Survey
    fieldsPair = Fields_ky
    _Jmatrix = None
    _f = None
    sign = None

    def fields(self, m):
        if self.verbose:
            print (">> Compute DC fields")

        if self._f is None:
            self._f = self.fieldsPair(self.mesh, self.survey)
            Srcs = self.survey.srcList
            for iky in range(self.nky):
                ky = self.kys[iky]
                A = self.getA(ky)
                self.Ainv[iky] = self.Solver(A, **self.solverOpts)
                RHS = self.getRHS(ky)
                u = self.Ainv[iky] * RHS
                self._f[Srcs, self._solutionType, iky] = u
        return self._f

    def getJ(self, m, f=None):
        """
            Generate Full sensitivity matrix
        """

        if self.verbose:
            print (">> Compute Sensitivity matrix")

        if self._Jmatrix is not None:
            return self._Jmatrix
        else:

            if f is None:
                f = self.fields(m)

            Jt = []

            # Assume y=0.
            # This needs some thoughts to implement in general when src is dipole
            dky = np.diff(self.kys)
            dky = np.r_[dky[0], dky]
            y = 0.
            for src in self.survey.srcList:
                for rx in src.rxList:
                    Jtv_temp1 = np.zeros((self.model.size, rx.nD), dtype=float)
                    Jtv_temp0 = np.zeros((self.model.size, rx.nD), dtype=float)
                    Jtv = np.zeros((self.model.size, rx.nD), dtype=float)
                    # TODO: this loop is pretty slow .. (Parellize)
                    for iky in range(self.nky):
                        u_src = f[src, self._solutionType, iky]
                        ky = self.kys[iky]

                        # wrt f, need possibility wrt m
                        P = rx.getP(self.mesh, rx.projGLoc(f)).toarray()

                        ATinvdf_duT = self.Ainv[iky] * (P.T)

                        dA_dmT = self.getADeriv(
                            ky, u_src, ATinvdf_duT, adjoint=True
                        )
                        Jtv_temp1 = 1./np.pi*(-dA_dmT)
                        if rx.nD == 1:
                            Jtv_temp1 = Jtv_temp1.reshape([-1, 1])

                        # Trapezoidal intergration
                        if iky == 0:
                            # First assigment
                            Jtv += Jtv_temp1*dky[iky]*np.cos(ky*y)
                        else:
                            Jtv += Jtv_temp1*dky[iky]/2.*np.cos(ky*y)
                            Jtv += Jtv_temp0*dky[iky]/2.*np.cos(ky*y)
                        Jtv_temp0 = Jtv_temp1.copy()
                    Jt.append(Jtv)
            self._Jmatrix = np.hstack(Jt).T

            # delete fields after computing sensitivity
            del f
            if self._f is not None:
                del self._f
            # clean all factorization
            if self.Ainv[0] is not None:
                for i in range(self.nky):
                    self.Ainv[i].clean()
            return self._Jmatrix

    def Jvec(self, m, v, f=None):
        self.model = m
        J = self.getJ(m, f=f)
        Jv = J.dot(v)
        return self.sign * Jv

    def Jtvec(self, m, v, f=None):
        self.model = m
        J = self.getJ(m, f=f)
        Jtv = J.T.dot(v)
        return self.sign * Jtv

    @property
    def deleteTheseOnModelUpdate(self):
        toDelete = []
        return toDelete

    @property
    def MfRhoDerivMat(self):
        """
        Derivative of MfRho with respect to the model
        """
        if getattr(self, '_MfRhoDerivMat', None) is None:
            drho_dlogrho = Utils.sdiag(self.rho)*self.etaDeriv
            self._MfRhoDerivMat = self.mesh.getFaceInnerProductDeriv(
                np.ones(self.mesh.nC)
            )(np.ones(self.mesh.nF)) * Utils.sdiag(self.rho) * self.etaDeriv
        return self._MfRhoDerivMat

    def MfRhoIDeriv(self, u, v, adjoint=False):
        """
            Derivative of :code:`MfRhoI` with respect to the model.
        """
        dMfRhoI_dI = -self.MfRhoI**2
        if self.storeInnerProduct:
            if adjoint:
                return self.MfRhoDerivMat.T * (Utils.sdiag(u) * (dMfRhoI_dI.T * v))
            else:
                return dMfRhoI_dI * (Utils.sdiag(u) * (self.MfRhoDerivMat*v))
        else:
            dMf_drho = self.mesh.getFaceInnerProductDeriv(self.rho)(u)
            drho_dlogrho = Utils.sdiag(self.rho)*self.etaDeriv
            if adjoint:
                return drho_dlogrho.T * (dMf_drho.T * (dMfRhoI_dI.T*v))
            else:
                return dMfRhoI_dI * (dMf_drho * (drho_dlogrho*v))

    @property
    def MeSigmaDerivMat(self):
        """
        Derivative of MeSigma with respect to the model
        """
        if getattr(self, '_MeSigmaDerivMat', None) is None:
            dsigma_dlogsigma = Utils.sdiag(self.sigma)*self.etaDeriv
            self._MeSigmaDerivMat = self.mesh.getEdgeInnerProductDeriv(
                np.ones(self.mesh.nC)
            )(np.ones(self.mesh.nE)) * dsigma_dlogsigma
        return self._MeSigmaDerivMat

    # TODO: This should take a vector
    def MeSigmaDeriv(self, u, v, adjoint=False):
        """
        Derivative of MeSigma with respect to the model times a vector (u)
        """
        if self.storeInnerProduct:
            if adjoint:
                return self.MeSigmaDerivMat.T * (Utils.sdiag(u)*v)
            else:
                return Utils.sdiag(u)*(self.MeSigmaDerivMat * v)
        else:
            dsigma_dlogsigma = Utils.sdiag(self.sigma)*self.etaDeriv
            if adjoint:
                return (
                    dsigma_dlogsigma.T * (
                        self.mesh.getEdgeInnerProductDeriv(self.sigma)(u).T * v
                    )
                )
            else:
                return (
                    self.mesh.getEdgeInnerProductDeriv(self.sigma)(u) *
                    (dsigma_dlogsigma * v)
                )

    @property
    def MccRhoiDerivMat(self):
        """
            Derivative of MccRho with respect to the model
        """
        if getattr(self, '_MccRhoiDerivMat', None) is None:
            rho = self.rho
            vol = self.mesh.vol
            drho_dlogrho = Utils.sdiag(rho)*self.etaDeriv
            self._MccRhoiDerivMat = (
                Utils.sdiag(vol*(-1./rho**2))*drho_dlogrho
            )
        return self._MccRhoiDerivMat

    def MccRhoiDeriv(self, u, v, adjoint=False):
        """
            Derivative of :code:`MccRhoi` with respect to the model.
        """
        if len(self.rho.shape) > 1:
            if self.rho.shape[1] > self.mesh.dim:
                raise NotImplementedError(
                    "Full anisotropy is not implemented for MccRhoiDeriv."
                )
        if self.storeInnerProduct:
            if adjoint:
                return self.MccRhoiDerivMat.T * (Utils.sdiag(u) * v)
            else:
                return Utils.sdiag(u) * (self.MccRhoiDerivMat * v)
        else:
            vol = self.mesh.vol
            rho = self.rho
            drho_dlogrho = Utils.sdiag(rho)*self.etaDeriv
            if adjoint:
                return drho_dlogrho.T * (u*vol*(-1./rho**2) * v)
            else:
                return (u*vol*(-1./rho**2))*(drho_dlogrho * v)

    @property
    def MnSigmaDerivMat(self):
        """
            Derivative of MnSigma with respect to the model
        """
        if getattr(self, '_MnSigmaDerivMat', None) is None:
            sigma = self.sigma
            vol = self.mesh.vol
            dsigma_dlogsigma = Utils.sdiag(sigma)*self.etaDeriv
            self._MnSigmaDerivMat = (
                self.mesh.aveN2CC.T * Utils.sdiag(vol) * dsigma_dlogsigma
                )
        return self._MnSigmaDerivMat

    def MnSigmaDeriv(self, u, v, adjoint=False):
        """
            Derivative of MnSigma with respect to the model times a vector (u)
        """
        if self.storeInnerProduct:
            if adjoint:
                return self.MnSigmaDerivMat.T * (Utils.sdiag(u)*v)
            else:
                return Utils.sdiag(u)*(self.MnSigmaDerivMat * v)
        else:
            sigma = self.sigma
            vol = self.mesh.vol
            dsigma_dlogsigma = Utils.sdiag(sigma)*self.etaDeriv
            if adjoint:
                return dsigma_dlogsigma.T * (vol * (self.mesh.aveN2CC * (u*v)))
            else:
                dsig_dm_v = dsigma_dlogsigma * v
                return (
                    u * (self.mesh.aveN2CC.T * (vol * dsig_dm_v))
                )


class Problem2D_CC(BaseIPProblem_2D, Problem2D_CC):
    """
    2.5D cell centered IP problem
    """

    _solutionType = 'phiSolution'
    _formulation = 'HJ'  # CC potentials means J is on faces
    fieldsPair = Fields_ky_CC
    bc_type = 'Mixed'
    sign = 1.

    def __init__(self, mesh, **kwargs):
        BaseIPProblem_2D.__init__(self, mesh, **kwargs)

    # def getA(self, ky):
    #     """

    #     Make the A matrix for the cell centered DC resistivity problem

    #     A = D MfRhoI G

    #     """
    #     # To handle Mixed boundary condition
    #     self.setBC(ky=ky)

    #     D = self.Div
    #     G = self.Grad
    #     vol = self.mesh.vol
    #     MfRhoI = self.MfRhoI
    #     # Get resistivity rho
    #     rho = self.rho
    #     A = D * MfRhoI * G + ky**2 * self.MccRhoi
    #     if self.bc_type == "Neumann":
    #         A[0, 0] = A[0, 0] + 1.
    #     return A

    # def getADeriv(self, ky, u, v, adjoint=False):

    #     # To handle Mixed boundary condition
    #     self.setBC(ky=ky)

    #     D = self.Div
    #     G = self.Grad
    #     vol = self.mesh.vol
    #     if adjoint:
    #         ADeriv = (
    #             self.MfRhoIDeriv(G*u, D.T*v, adjoint) +
    #             ky**2 * self.MccRhoiDeriv(u, v, adjoint)
    #         )
    #     else:
    #         ADeriv = (
    #             D * self.MfRhoIDeriv(G*u, v, adjoint) +
    #             ky**2*self.MccRhoiDeriv(u, v, adjoint)
    #         )
    #     return ADeriv

    # def getRHS(self, ky):
    #     """
    #     RHS for the DC problem

    #     q
    #     """

    #     RHS = self.getSourceTerm(ky)
    #     return RHS

    # def getRHSDeriv(self, ky, src, v, adjoint=False):
    #     """
    #     Derivative of the right hand side with respect to the model
    #     """
    #     # TODO: add qDeriv for RHS depending on m
    #     # qDeriv = src.evalDeriv(self, ky, adjoint=adjoint)
    #     # return qDeriv
    #     return Zero()

    # def setBC(self, ky=None):
    #     fxm, fxp, fym, fyp = self.mesh.faceBoundaryInd
    #     gBFxm = self.mesh.gridFx[fxm, :]
    #     gBFxp = self.mesh.gridFx[fxp, :]
    #     gBFym = self.mesh.gridFy[fym, :]
    #     gBFyp = self.mesh.gridFy[fyp, :]

    #     # Setup Mixed B.C (alpha, beta, gamma)
    #     temp_xm = np.ones_like(gBFxm[:, 0])
    #     temp_xp = np.ones_like(gBFxp[:, 0])
    #     temp_ym = np.ones_like(gBFym[:, 1])
    #     temp_yp = np.ones_like(gBFyp[:, 1])

    #     if self.bc_type == "Neumann":
    #         alpha_xm, alpha_xp = temp_xm*0., temp_xp*0.
    #         alpha_ym, alpha_yp = temp_ym*0., temp_yp*0.

    #         beta_xm, beta_xp = temp_xm, temp_xp
    #         beta_ym, beta_yp = temp_ym, temp_yp

    #         gamma_xm, gamma_xp = temp_xm*0., temp_xp*0.
    #         gamma_ym, gamma_yp = temp_ym*0., temp_yp*0.

    #     elif self.bc_type == "Dirichlet":
    #         alpha_xm, alpha_xp = temp_xm, temp_xp
    #         alpha_ym, alpha_yp = temp_ym, temp_yp

    #         beta_xm, beta_xp = temp_xm*0., temp_xp*0.
    #         beta_ym, beta_yp = temp_ym*0., temp_yp*0.

    #         gamma_xm, gamma_xp = temp_xm*0., temp_xp*0.
    #         gamma_ym, gamma_yp = temp_ym*0., temp_yp*0.

    #     elif self.bc_type == "Mixed":
    #         xs = np.median(self.mesh.vectorCCx)
    #         ys = np.median(self.mesh.vectorCCy[-1])

    #         def r_boundary(x, y):
    #             return 1./np.sqrt(
    #                 (x - xs)**2 + (y - ys)**2
    #                 )

    #         rxm = r_boundary(gBFxm[:, 0], gBFxm[:, 1])
    #         rxp = r_boundary(gBFxp[:, 0], gBFxp[:, 1])
    #         rym = r_boundary(gBFym[:, 0], gBFym[:, 1])
    #         alpha_xm = ky*(
    #             kn(1, ky*rxm) / kn(0, ky*rxm) * (gBFxm[:, 0]-xs)
    #             )
    #         alpha_xp = ky*(
    #             kn(1, ky*rxp) / kn(0, ky*rxp) * (gBFxp[:, 0]-xs)
    #             )
    #         alpha_ym = ky*(
    #             kn(1, ky*rym) / kn(0, ky*rym) * (gBFym[:, 0]-ys)
    #             )
    #         alpha_yp = temp_yp*0.
    #         beta_xm, beta_xp = temp_xm, temp_xp
    #         beta_ym, beta_yp = temp_ym, temp_yp

    #         gamma_xm, gamma_xp = temp_xm*0., temp_xp*0.
    #         gamma_ym, gamma_yp = temp_ym*0., temp_yp*0.

    #     alpha = [alpha_xm, alpha_xp, alpha_ym, alpha_yp]
    #     beta = [beta_xm, beta_xp, beta_ym, beta_yp]
    #     gamma = [gamma_xm, gamma_xp, gamma_ym, gamma_yp]

    #     x_BC, y_BC = getxBCyBC_CC(self.mesh, alpha, beta, gamma)
    #     V = self.Vol
    #     self.Div = V * self.mesh.faceDiv
    #     P_BC, B = self.mesh.getBCProjWF_simple()
    #     M = B*self.mesh.aveCC2F
    #     self.Grad = self.Div.T - P_BC*Utils.sdiag(y_BC)*M

    def delete_these_for_sensitivity(self, sigma=None, rho=None):
        if self._Jmatrix is not None:
            del self._Jmatrix
        self._MfrhoI = None
        if sigma is not None:
            self.sigma = sigma
        elif rho is not None:
            self.sigma = 1./rho
        else:
            raise Exception("Either sigma or rho should be provided")


class Problem2D_N(BaseIPProblem_2D, Problem2D_N):
    """
    2.5D nodal IP problem
    """

    _solutionType = 'phiSolution'
    _formulation = 'EB'  # CC potentials means J is on faces
    fieldsPair = Fields_ky_N
    sign = -1.

    def __init__(self, mesh, **kwargs):
        BaseIPProblem_2D.__init__(self, mesh, **kwargs)

    # def getA(self, ky):
    #     """

    #     Make the A matrix for the cell centered DC resistivity problem

    #     A = D MfRhoI G

    #     """

    #     MeSigma = self.MeSigma
    #     MnSigma = self.MnSigma
    #     Grad = self.mesh.nodalGrad
    #     A = Grad.T * MeSigma * Grad + ky**2*MnSigma

    #     # Handling Null space of A
    #     A[0, 0] = A[0, 0] + 1.
    #     return A

    # @Utils.count
    # @Utils.timeIt
    # def getADeriv(self, ky, u, v, adjoint=False):

    #     MeSigma = self.MeSigma
    #     Grad = self.mesh.nodalGrad
    #     sigma = self.sigma
    #     vol = self.mesh.vol
    #     if adjoint:
    #         return (self.MeSigmaDeriv(Grad*u, Grad*v, adjoint) +
    #                 ky**2*self.MnSigmaDeriv(u, v, adjoint))

    #     return (Grad.T*(self.MeSigmaDeriv(Grad*u, v, adjoint)) +
    #             ky**2*self.MnSigmaDeriv(u, v, adjoint))

    # def getRHS(self, ky):
    #     """
    #     RHS for the DC problem

    #     q
    #     """

    #     RHS = self.getSourceTerm(ky)
    #     return RHS

    # def getRHSDeriv(self, ky, src, v, adjoint=False):
    #     """
    #     Derivative of the right hand side with respect to the model
    #     """
    #     # TODO: add qDeriv for RHS depending on m
    #     # qDeriv = src.evalDeriv(self, ky, adjoint=adjoint)
    #     # return qDeriv
    #     return Zero()

    def delete_these_for_sensitivity(self, sigma=None, rho=None):
        if self._Jmatrix is not None:
            del self._Jmatrix
        self._MeSigma = None
        self._MnSigma = None
        if sigma is not None:
            self.sigma = sigma
        elif rho is not None:
            self.sigma = 1./rho
        else:
            raise Exception("Either sigma or rho should be provided")