import unittest
import os
import time
import local_cache_db
import image_search

class TestAutomationFlow(unittest.TestCase):
    def setUp(self):
        local_cache_db.init_db()

    def test_automation_state_crud(self):
        # 1. Update state
        success = local_cache_db.update_automation_state(
            status='pre_caching',
            total=10,
            processed=2,
            success=1,
            failed=1,
            current_product='Test Product'
        )
        self.assertTrue(success)

        # 2. Get state
        state = local_cache_db.get_automation_state()
        self.assertEqual(state['status'], 'pre_caching')
        self.assertEqual(state['total_items'], 10)
        self.assertEqual(state['processed_items'], 2)
        self.assertEqual(state['success_count'], 1)
        self.assertEqual(state['failed_count'], 1)
        self.assertEqual(state['current_product_name'], 'Test Product')

    def test_pause_resume_mechanics(self):
        # 1. Pause
        self.assertTrue(local_cache_db.pause_automation())
        state = local_cache_db.get_automation_state()
        self.assertEqual(state['pause_requested'], 1)

        # 2. Resume
        self.assertTrue(local_cache_db.resume_automation())
        state = local_cache_db.get_automation_state()
        self.assertEqual(state['pause_requested'], 0)

    def test_search_throttling_delay(self):
        # Verify run_parallel_consensus_search delay (min 1.5 seconds)
        original_aggregate = image_search.ParallelConsensusScraper.aggregate_consensus_rankings
        async def mock_aggregate(self, query):
            return []
        
        image_search.ParallelConsensusScraper.aggregate_consensus_rankings = mock_aggregate
        
        try:
            start_time = time.time()
            res = image_search.run_parallel_consensus_search("test_throttling_query")
            elapsed = time.time() - start_time
            
            self.assertGreaterEqual(elapsed, 1.45)
            self.assertEqual(res, [])
        finally:
            image_search.ParallelConsensusScraper.aggregate_consensus_rankings = original_aggregate

if __name__ == '__main__':
    unittest.main()
