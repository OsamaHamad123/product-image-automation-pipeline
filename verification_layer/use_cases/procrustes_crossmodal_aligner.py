# verification_layer/use_cases/procrustes_crossmodal_aligner.py
import numpy as np
from dataclasses import dataclass
from typing import Tuple


@dataclass
class ProcrustesAlignmentResult:
    orthogonal_matrix_Q: np.ndarray
    infonce_loss: float
    alignment_frobenius_error: float
    is_orthogonal: bool


class SVDOrthogonalProcrustesAligner:
    """
    محاذاة بروكروستس المتعامدة لتكامل الفضاءات المتجهية ثنائية اللغة (Orthogonal Procrustes Alignment via SVD)
    - Y * X^T = U * Sigma * V^T  ==>  Q* = U * V^T
    - InfoNCE Loss: L_InfoNCE = -1/2 sum( log(...) + log(...) )
    """

    @classmethod
    def calculate_orthogonal_procrustes(cls, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
        """
        X: Source embeddings (D x N)
        Y: Target embeddings (D x N)
        Returns orthogonal rotation matrix Q* (D x D) such that Q* * Q*^T = I
        """
        M = np.dot(Y, X.T)
        U, _, Vt = np.linalg.svd(M)
        Q_star = np.dot(U, Vt)
        return Q_star

    @classmethod
    def calculate_infonce_loss(cls, Z_visual: np.ndarray, Z_text: np.ndarray, tau: float = 0.07) -> float:
        """
        Z_visual: (B x D)
        Z_text: (B x D)
        """
        B = Z_visual.shape[0]
        if B == 0:
            return 0.0

        # Cosine similarity matrix B x B
        sim_vt = np.dot(Z_visual, Z_text.T) / tau
        sim_tv = np.dot(Z_text, Z_visual.T) / tau

        # Softmax loss
        exp_vt = np.exp(sim_vt - np.max(sim_vt, axis=1, keepdims=True))
        exp_tv = np.exp(sim_tv - np.max(sim_tv, axis=1, keepdims=True))

        prob_vt = np.diag(exp_vt) / np.sum(exp_vt, axis=1)
        prob_tv = np.diag(exp_tv) / np.sum(exp_tv, axis=1)

        loss_v = -np.mean(np.log(prob_vt + 1e-8))
        loss_t = -np.mean(np.log(prob_tv + 1e-8))

        infonce_loss = 0.5 * (loss_v + loss_t)
        return float(round(infonce_loss, 4))

    @classmethod
    def align_and_evaluate(cls, X_source: np.ndarray, Y_target: np.ndarray) -> ProcrustesAlignmentResult:
        Q_star = cls.calculate_orthogonal_procrustes(X_source, Y_target)

        # Verify orthogonality: Q * Q^T should be Identity matrix I
        I_approx = np.dot(Q_star, Q_star.T)
        is_ortho = bool(np.allclose(I_approx, np.eye(Q_star.shape[0]), atol=1e-4))

        # Aligned X
        X_aligned = np.dot(Q_star, X_source)
        frob_err = float(np.linalg.norm(X_aligned - Y_target, 'fro'))

        loss = cls.calculate_infonce_loss(X_aligned.T, Y_target.T)

        return ProcrustesAlignmentResult(
            orthogonal_matrix_Q=Q_star,
            infonce_loss=loss,
            alignment_frobenius_error=round(frob_err, 4),
            is_orthogonal=is_ortho,
        )
