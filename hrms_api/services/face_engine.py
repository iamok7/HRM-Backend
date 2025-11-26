import logging
import numpy as np
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

class FaceEngine:
    """
    Wrapper around DeepFace for face verification.
    """
    
    MODEL_NAME = "Facenet512" 
    DETECTOR_BACKEND = "opencv" # Fast
    
    @staticmethod
    def get_embedding(image_path_or_data: Union[str, np.ndarray]) -> Optional[List[float]]:
        """
        Generate embedding for a single face in the image.
        Returns None if no face or multiple faces found.
        """
        # Lazy import to avoid startup overhead if not used
        try:
            from deepface import DeepFace
        except ImportError:
            logger.error("DeepFace not installed. Please install deepface.")
            return None
        
        try:
            results = DeepFace.represent(
                img_path=image_path_or_data,
                model_name=FaceEngine.MODEL_NAME,
                enforce_detection=True,
                detector_backend=FaceEngine.DETECTOR_BACKEND
            )
            
            if not results:
                logger.warning("No face detected.")
                return None
                
            if len(results) > 1:
                logger.warning(f"Multiple faces detected: {len(results)}")
                return None
                
            return results[0]["embedding"]
            
        except ValueError as ve:
            # DeepFace raises ValueError if face could not be detected when enforce_detection=True
            logger.warning(f"Face detection failed: {ve}")
            return None
        except Exception as e:
            logger.error(f"Face engine error: {e}")
            return None

    @staticmethod
    def compute_similarity(emb1: List[float], emb2: List[float]) -> float:
        """
        Compute cosine similarity between two embeddings.
        Returns a value between -1 and 1 (1 means identical).
        """
        a = np.array(emb1)
        b = np.array(emb2)
        
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
            
        return float(np.dot(a, b) / (norm_a * norm_b))
