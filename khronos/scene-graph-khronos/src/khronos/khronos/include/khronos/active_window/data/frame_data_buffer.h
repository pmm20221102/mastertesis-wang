/** -----------------------------------------------------------------------------
 * Copyright (c) 2024 Massachusetts Institute of Technology.
 * All Rights Reserved.
 *
 * AUTHORS:      Lukas Schmid <lschmid@mit.edu>, Marcus Abate <mabate@mit.edu>,
 *               Yun Chang <yunchang@mit.edu>, Luca Carlone <lcarlone@mit.edu>
 * AFFILIATION:  MIT SPARK Lab, Massachusetts Institute of Technology
 * YEAR:         2024
 * SOURCE:       https://github.com/MIT-SPARK/Khronos
 * LICENSE:      BSD 3-Clause
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice, this
 * list of conditions and the following disclaimer.
 *
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 * this list of conditions and the following disclaimer in the documentation
 * and/or other materials provided with the distribution.
 *
 * 3. Neither the name of the copyright holder nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
 * AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
 * IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
 * FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
 * DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
 * SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
 * CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
 * OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 * -------------------------------------------------------------------------- */

#pragma once

#include <deque>
#include <vector>

#include "khronos/active_window/data/frame_data.h"
#include "khronos/active_window/data/track.h"
#include "khronos/common/common_types.h"

namespace khronos {

/** @brief Keeps frames in memory until no active track references them. */
class FrameDataBuffer {
 public:
  struct Config {
    // Maximum number of frames to keep in the buffer.
    size_t max_buffer_size = 300;

    // Store only every nth frame to reduce memory use.
    int store_every_n_frames = 1;
  } const config;

  explicit FrameDataBuffer(const Config& config);
  virtual ~FrameDataBuffer() = default;

  //! Get the latest frame data.
  const FrameData& getLatestData() const { return *buffer_.back(); }

  //! Add a new frame as a candidate for the buffer.
  void storeData(const FrameData::Ptr& data);

  //! Remove frame data no longer referenced by any track; call before adding data.
  void trimBuffer(const Tracks& tracks);

  //! Get frame data at the requested timestamp, or nullptr if it is unavailable.
  FrameData::Ptr getData(const TimeStamp time_stamp) const;

  size_t size() const { return buffer_.size(); }

  using const_iterator = std::deque<FrameData::Ptr>::const_iterator;
  const_iterator begin() const { return buffer_.begin(); }
  const_iterator end() const { return buffer_.end(); }

 private:
  std::deque<FrameData::Ptr> buffer_;
  TimeStamp oldest_time_stamp_ = 0;
  int input_counter_ = 0;
};

void declare_config(FrameDataBuffer::Config& config);

}  // namespace khronos
